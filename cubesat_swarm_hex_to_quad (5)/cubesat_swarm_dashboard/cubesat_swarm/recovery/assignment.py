"""Capability-constrained mission role assignment using exact small-set search.

For six spacecraft, exact enumeration makes assignment decisions deterministic and
inspectable. The score combines target distance, role-switch cost, energy reserve,
remaining delta-v, estimated ISL quality, maneuvering ability, and marginal sensor
coverage contribution. The weights are tunable software policy values, not flight
validated mission constants.
"""
from __future__ import annotations
from dataclasses import dataclass
from itertools import permutations
from typing import Iterable, Mapping
import math

from ..models import Satellite

@dataclass(frozen=True)
class Role:
    name: str
    required_any: frozenset[str]
    priority: int
    target_y_m: float

ROLES = (
    Role("COORDINATOR", frozenset({"compute"}), 0, -12_500.0),
    Role("MAPPER_1", frozenset({"observation_highres", "observation_wide"}), 1, -7_500.0),
    Role("MAPPER_2", frozenset({"observation_highres", "observation_wide"}), 2, -2_500.0),
    Role("TRACKER_1", frozenset({"observation_wide", "observation_highres"}), 3, 2_500.0),
    Role("TRACKER_2", frozenset({"observation_wide", "observation_highres"}), 4, 7_500.0),
    Role("RELAY", frozenset({"isl"}), 5, 12_500.0),
)
CRITICAL_ROLES = ("COORDINATOR", "MAPPER_1", "MAPPER_2", "RELAY")
ROLE_BY_NAME = {r.name: r for r in ROLES}

@dataclass
class AssignmentResult:
    feasible: bool
    assignment: dict[str, str]
    active_roles: list[str]
    cost: float
    reason: str = ""

class RoleAssigner:
    """Select a feasible, unique role map with an auditable multi-term cost."""
    def __init__(self, switch_penalty: float = 2_000.0, distance_weight: float = 1.0,
                 energy_weight: float = 10.0, delta_v_weight: float = 200.0,
                 comm_weight_critical: float = 12.0, comm_weight_other: float = 6.0,
                 coverage_reward_weight: float = 8.0, maneuver_penalty: float = 2_000.0,
                 reference_delta_v_mps: float = 10.0):
        self.switch_penalty = float(switch_penalty)
        self.distance_weight = float(distance_weight)
        self.energy_weight = float(energy_weight)
        self.delta_v_weight = float(delta_v_weight)
        self.comm_weight_critical = float(comm_weight_critical)
        self.comm_weight_other = float(comm_weight_other)
        self.coverage_reward_weight = float(coverage_reward_weight)
        self.maneuver_penalty = float(maneuver_penalty)
        self.reference_delta_v_mps = float(reference_delta_v_mps)
        values = (self.switch_penalty, self.distance_weight, self.energy_weight,
                  self.delta_v_weight, self.comm_weight_critical, self.comm_weight_other,
                  self.coverage_reward_weight, self.maneuver_penalty, self.reference_delta_v_mps)
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("assignment score weights and reference budget must be finite and non-negative")

    @staticmethod
    def communication_quality_pct(sat: Satellite, active: list[Satellite],
                                  communication_range_m: float, packet_loss_probability: float,
                                  isolated_ids: set[str] | frozenset[str] = frozenset()) -> float:
        if ("isl" not in sat.effective_capabilities or communication_range_m <= 0
                or sat.satellite_id in isolated_ids):
            return 0.0
        qualities = []
        for other in active:
            if (other.satellite_id == sat.satellite_id or other.satellite_id in isolated_ids
                    or "isl" not in other.effective_capabilities):
                continue
            distance = float(math.hypot(float(sat.state[0]-other.state[0]), float(sat.state[1]-other.state[1])))
            if distance <= communication_range_m:
                qualities.append(max(0.0, 1.0-distance/communication_range_m) * (1.0-packet_loss_probability) * 100.0)
        # A singleton network has no peer link. Return zero to make the limitation
        # visible to the role manager rather than fabricating link quality.
        return float(sum(qualities)/len(qualities)) if qualities else 0.0

    def score_candidate(self, sat: Satellite, role: Role, previous: dict[str, str] | None = None,
                        active: list[Satellite] | None = None,
                        communication_range_m: float = 18_000.0,
                        packet_loss_probability: float = 0.0,
                        coverage_contributions: Mapping[str, float] | None = None,
                        isolated_ids: set[str] | frozenset[str] = frozenset()) -> dict[str, float | bool]:
        """Return individual score terms for one satellite-role candidate."""
        previous = previous or {}
        active = active if active is not None else [s for s in [sat] if s.is_operational]
        coverage_contributions = coverage_contributions or {}
        eligible = sat.is_operational and bool(sat.effective_capabilities & role.required_any)
        distance = float(math.hypot(float(sat.state[0]), float(sat.state[1])-role.target_y_m))
        distance_cost = self.distance_weight * distance
        switch_cost = self.switch_penalty if previous.get(sat.satellite_id) not in (None, role.name) else 0.0
        energy_penalty = max(0.0, 100.0-float(sat.energy_percent)) * self.energy_weight
        dv_shortfall = max(0.0, self.reference_delta_v_mps-float(sat.remaining_dv_mps))
        delta_v_penalty = dv_shortfall * self.delta_v_weight
        comm_quality = self.communication_quality_pct(sat, active, float(communication_range_m),
                                                      float(packet_loss_probability), isolated_ids)
        comm_weight = self.comm_weight_critical if role.name in {"COORDINATOR", "RELAY"} else self.comm_weight_other
        comm_penalty = max(0.0, 100.0-comm_quality) * comm_weight
        needs_maneuver = distance > 1_000.0
        maneuver_penalty = self.maneuver_penalty if needs_maneuver and (sat.remaining_dv_mps <= 0 or sat.effective_max_acceleration_mps2 <= 0) else 0.0
        marginal_coverage_pct = float(coverage_contributions.get(sat.satellite_id, 0.0))
        coverage_reward = self.coverage_reward_weight * marginal_coverage_pct if role.name.startswith(("MAPPER", "TRACKER")) else 0.0
        total = distance_cost + switch_cost + energy_penalty + delta_v_penalty + comm_penalty + maneuver_penalty - coverage_reward
        if not eligible:
            total = math.inf
        return {
            "eligible": bool(eligible),
            "distance_m": distance,
            "distance_cost": distance_cost,
            "switch_penalty": switch_cost,
            "energy_penalty": energy_penalty,
            "delta_v_penalty": delta_v_penalty,
            "communication_quality_pct": comm_quality,
            "communication_penalty": comm_penalty,
            "maneuver_penalty": maneuver_penalty,
            "coverage_contribution_pct": marginal_coverage_pct,
            "coverage_reward": coverage_reward,
            "total_cost": total,
        }

    def assign(self, satellites: Iterable[Satellite], previous: dict[str, str] | None = None,
               communication_range_m: float = 18_000.0,
               packet_loss_probability: float = 0.0,
               coverage_contributions: Mapping[str, float] | None = None,
               isolated_ids: set[str] | frozenset[str] = frozenset()) -> AssignmentResult:
        active = sorted((s for s in satellites if s.is_operational), key=lambda s: s.satellite_id)
        previous = previous or {}
        if len(active) < 4:
            return AssignmentResult(False, {}, [], math.inf,
                f"Only {len(active)} spacecraft remain; four critical roles are required.")

        # Try the richest mission-role set first. If a payload has failed while
        # its bus remains operational, optional tracking roles can be left inactive
        # instead of making otherwise-feasible critical recovery fail.
        if len(active) >= 6:
            role_plans = [
                [r.name for r in ROLES],
                ["COORDINATOR", "MAPPER_1", "MAPPER_2", "TRACKER_1", "RELAY"],
                list(CRITICAL_ROLES),
            ]
        elif len(active) == 5:
            role_plans = [
                ["COORDINATOR", "MAPPER_1", "MAPPER_2", "TRACKER_1", "RELAY"],
                list(CRITICAL_ROLES),
            ]
        else:
            role_plans = [list(CRITICAL_ROLES)]

        for role_names in role_plans:
            role_defs = [ROLE_BY_NAME[n] for n in role_names]
            best_cost = math.inf
            best: dict[str, str] | None = None
            for chosen in permutations(active, len(role_defs)):
                mapping: dict[str, str] = {}
                cost = 0.0
                valid = True
                for role, sat in zip(role_defs, chosen, strict=True):
                    terms = self.score_candidate(sat, role, previous, active, communication_range_m,
                                                 packet_loss_probability, coverage_contributions, isolated_ids)
                    if not terms["eligible"]:
                        valid = False
                        break
                    cost += float(terms["total_cost"])
                    mapping[sat.satellite_id] = role.name
                if valid and cost < best_cost:
                    best_cost, best = cost, mapping
            if best is not None:
                return AssignmentResult(True, best, role_names, best_cost,
                    "feasible" if len(role_names) == len(active) else
                    f"feasible with {len(role_names)} active roles; lower-priority roles left inactive because of capability limits")

        return AssignmentResult(False, {}, role_plans[-1], math.inf,
            "No capability-feasible assignment satisfies the four required critical roles.")
