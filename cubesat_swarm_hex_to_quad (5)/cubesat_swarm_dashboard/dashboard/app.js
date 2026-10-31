'use strict';

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const state = { data: null, grid: null, gridTime: -Infinity, currentView: 'overview', polling: false, busy: false, resetPending: false, toastTimer: null, settingsInitialized: false, settingsDirty: false, lastTickAt: null };
const viewMeta = {
  overview: ['Swarm mission overview', 'Live state, health, formation, and recovery performance of a six-spacecraft swarm.'],
  fleet: ['Satellite fleet telemetry', 'Current physical state, hardware capabilities, normalized energy proxy and sensor contributions.'],
  failures: ['Failure detection & fault injection', 'Separate confirmed modeled failures from communication suspicions and temporary outages.'],
  roles: ['Autonomous role manager', 'Capability-constrained assignment, candidate suitability costs, and decision history.'],
  formation: ['Formation reconfiguration', 'Positions, target errors, bounded control inputs, pairwise separation, and convergence monitoring.'],
  coverage: ['Mission coverage optimizer', 'Grid coverage, sensor footprints, observation points, uncovered region and performance over time.'],
  communications: ['Inter-satellite communications', 'Heartbeat transport, range-limited network topology, packet loss and reversible communication outages.'],
  recovery: ['Recovery verification', 'Evaluate the end-to-end recovery criteria using the current simulated state.'],
  settings: ['Mission configuration', 'Change the model parameters and rerun the configured six-satellite scenario.']
};
const colors = { cyan: '#6be2d2', blue: '#83a9ff', amber: '#ffbf69', red: '#ff7185', green: '#73e0ac', muted: '#75869f', grid: '#24334a', text: '#e6edf8' };

function esc(v) { return String(v ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); }
function fmt(v, decimals = 1) { const n = Number(v); return Number.isFinite(n) ? n.toLocaleString('en-US', {minimumFractionDigits: decimals, maximumFractionDigits: decimals}) : '—'; }
function fmtKm(m, decimals = 2) { return Number.isFinite(Number(m)) ? `${fmt(Number(m) / 1000, decimals)} km` : '—'; }
function pct(v, decimals = 1) { return `${fmt(v, decimals)}%`; }
function timeCode(seconds) { const n = Math.max(0, Number(seconds) || 0); const minutes = Math.floor(n / 60); const remainder = n - minutes * 60; return `${String(minutes).padStart(2,'0')}:${remainder.toFixed(1).padStart(4,'0')}`; }
function statusClass(text) { const s = String(text || '').toLowerCase(); if (s.includes('failed') || s.includes('degraded') || s.includes('below') || s.includes('not safe') || s.includes('suspect') || s.includes('lost')) return 'bad'; if (s.includes('nominal') || s.includes('recovered') || s.includes('connected') || s.includes('pass') || s.includes('active') || s.includes('feasible') || s.includes('restored')) return 'good'; if (s.includes('reconfigur') || s.includes('ready') || s.includes('partial') || s.includes('paused')) return 'warn'; return 'neutral'; }
function toast(message, kind = 'info') { const node = $('#toast'); node.textContent = message; node.className = `toast show ${kind}`; clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => node.className = 'toast', 3500); }
async function api(url, body = undefined) { const opt = body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}; const r = await fetch(url, opt); const out = await r.json().catch(() => ({})); if (!r.ok) throw new Error(out.error || `Request failed (${r.status})`); return out; }
function setView(name) { state.currentView = name; $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === name)); $$('.view').forEach(v => v.classList.toggle('active', v.id === `view-${name}`)); $('#pageTitle').textContent = viewMeta[name][0]; $('#pageDescription').textContent = viewMeta[name][1]; $('#breadcrumbPage').textContent = name.toUpperCase().replaceAll('_',' '); if (state.data) render(state.data, true); if (name === 'coverage') loadGrid(true); }

function render(data, force = false) {
  if (!data) return;
  state.data = data;
  const sim = data.simulation, mission = data.mission, sats = data.satellites;
  $('#headerClock').textContent = timeCode(sim.time_s); $('#sideClock').textContent = timeCode(sim.time_s); $('#sideProgress').textContent = `${fmt(sim.progress_pct,0)}% elapsed`;
  $('#sideActive').textContent = mission.active_satellites; $('#sideActiveBar').style.width = `${100*mission.active_satellites/6}%`;
  $('#kpiActive').textContent = mission.active_satellites; $('#activeKpiBar').style.width = `${100*mission.active_satellites/6}%`;
  $('#activeFoot').textContent = mission.active_satellites === 6 ? 'All spacecraft in formation' : `${mission.failed_satellites} confirmed failure${mission.failed_satellites === 1 ? '' : 's'}`;
  $('#activeFootDot').className = `foot-indicator ${mission.active_satellites < 4 ? 'red' : mission.active_satellites < 6 ? 'amber' : ''}`;
  $('#kpiCoverage').textContent = fmt(mission.coverage_pct,1); $('#coverageKpiBar').style.width = `${mission.coverage_pct}%`;
  const delta = mission.coverage_delta_pct_points; $('#coverageDelta').textContent = `${delta >= 0 ? '+' : ''}${fmt(delta,1)} pp vs initial`; $('#coverageDelta').className = delta < -0.1 ? 'delta-negative' : 'delta-positive';
  $('#kpiConnectivity').textContent = fmt(mission.connected_fraction*100,0); $('#connectivityKpiBar').style.width = `${mission.connected_fraction*100}%`;
  const comm = data.communication; $('#kpiLinks').textContent = `${comm.link_up_count} live links`; $('#networkPill').textContent = comm.connected_fraction >= .999 ? 'CONNECTED' : 'PARTITIONED';
  $('#kpiFormation').textContent = fmt(mission.formation_error_rms_m,0); $('#formationKpiBar').style.width = `${Math.min(100, mission.formation_error_rms_m/1000*100)}%`;
  $('#kpiHeartbeat').textContent = fmt(mission.heartbeat_delivery_pct,0); $('#heartbeatKpiBar').style.width = `${mission.heartbeat_delivery_pct}%`; $('#heartbeatCount').textContent = `${mission.heartbeat_delivered} delivered / ${mission.heartbeat_dropped} dropped`;
  $('#kpiMissionStatus').textContent = mission.status; $('#kpiMissionStatus').className = `status-word ${statusClass(mission.status)}`; $('#statusUnderline').className = `status-underline ${statusClass(mission.status)}`;
  $('#kpiRecoveryFoot').textContent = `${mission.recovery_checks.filter(c => c.passed).length} of ${mission.recovery_checks.length} recovery checks passed`;
  const speedSelect=$('#liveSpeed'); const speedValue=String(sim.speed); if(![...speedSelect.options].some(o=>o.value===speedValue)){const option=document.createElement('option');option.value=speedValue;option.textContent=`${speedValue}×`;speedSelect.add(option);} speedSelect.value=speedValue;
  $('#runStatusText').textContent = sim.running ? 'RUNNING' : sim.finished ? 'COMPLETED' : sim.time_s > 0 ? 'PAUSED' : 'READY';
  $('#runStatusDot').className = `status-indicator ${sim.running ? 'live' : sim.finished ? 'finished' : 'idle'}`;
  $('#sidebarMode').textContent = sim.running ? 'SIMULATION LIVE' : sim.finished ? 'SIMULATION COMPLETE' : sim.time_s > 0 ? 'SIMULATION PAUSED' : 'SIMULATION STANDBY';
  $('#sidebarLiveDot').style.background = sim.running ? colors.green : sim.finished ? colors.blue : colors.muted;
  $('#startBtn').disabled = sim.running || sim.finished; $('#pauseBtn').disabled = !sim.running;
  $('#startBtn').innerHTML = sim.finished ? '<span>✓</span> Completed' : sim.time_s > 0 ? '<span>▶</span> Resume' : '<span>▶</span> Start';
  $('#lastUpdated').textContent = `Telemetry updated ${new Date().toLocaleTimeString()}`;
  $('#failureNavCount').textContent = String(mission.failed_satellites + mission.suspected_links).padStart(2,'0');
  renderOverview(data); renderFleet(data); renderFailures(data); renderRoles(data); renderFormation(data); renderCoverage(data); renderCommunications(data); renderRecovery(data); renderSettings(data);
  if (state.grid && (force || Math.abs(state.gridTime - sim.time_s) < 0.0001 || sim.time_s - state.gridTime >= .7)) drawCoverageMaps();
}

function renderOverview(d) {
  $('#formationChart').innerHTML = makeFormationSvg(d, 700, 365, false);
  $('#pipelineList').innerHTML = makePipeline(d);
  $('#overviewEvents').innerHTML = makeEvents(d.events.slice(0,6), true);
  $('#coveragePlotCaption').textContent = d.metrics.length > 1 ? `${d.metrics.length} samples` : 'Waiting for data';
  $('#coverageHistoryChart').innerHTML = makeLineSvg(d.metrics.map(x=>({x:x.time_s,y:x.coverage_fraction*100})), 'Coverage (%)');
  $('#overviewCoveredCells').textContent = Number(d.coverage.covered_cells).toLocaleString();
  $('#overviewUncovered').textContent = Number(d.coverage.uncovered_cells).toLocaleString(); $('#overviewSensors').textContent = `${d.coverage.active_sensors} / 6`;
}
function makePipeline(d) {
  const m=d.mission;
  const stages=[
    ['01','Initialize six CubeSats',true,`${6} initial states and capability profiles`],
    ['02','Monitor health & heartbeat',d.simulation.time_s>0 || d.simulation.finished,`${m.heartbeat_delivered} heartbeat deliveries recorded`],
    ['03','Detect and confirm failures',m.failed_satellites>0 || d.simulation.finished,`${m.failed_satellites} bus failure${m.failed_satellites===1?'':'s'} confirmed · ${m.suspected_links} link suspicions`],
    ['04','Reassign roles',d.assignment_matrix.some(x=>x.eligible) && d.simulation.time_s>0, d.mission.assignment_feasible?'Capability-feasible assignment':'Assignment infeasible / degraded'],
    ['05','Reconfigure formation',d.simulation.time_s>0,`RMS error ${fmt(m.formation_error_rms_m,0)} m`],
    ['06','Optimize coverage',d.simulation.time_s>0,`${fmt(m.coverage_pct,1)}% of abstract target region`],
    ['07','Verify recovery',d.simulation.time_s>0,`${m.recovery_checks.filter(c=>c.passed).length}/${m.recovery_checks.length} criteria passed`]
  ];
  return stages.map(([n,title,done,detail],i)=>`<div class="pipeline-step ${done?'done':''} ${i===stages.length-1&&m.recovery_passed?'passed':''}"><div class="pipeline-node">${done?'✓':n}</div><div class="pipeline-copy"><strong>${title}</strong><small>${detail}</small></div><span class="pipeline-status ${done?'good':'neutral'}">${done?(i===6?(m.recovery_passed?'PASS':'CHECK'): 'ACTIVE'):'PENDING'}</span></div>`).join('');
}
function renderFleet(d) {
  const sats=d.satellites; $('#fleetSummary').textContent=`${sats.filter(s=>s.is_operational).length} operational · ${sats.filter(s=>s.is_isolated).length} communication-isolated`;
  $('#fleetCards').innerHTML=sats.map(s=>`<article class="fleet-card ${s.is_operational?'':'is-failed'}"><div class="fleet-card-top"><div class="sat-id">${esc(s.satellite_id)}<span class="sat-orbit-mark">●</span></div><span class="status-badge ${statusClass(s.status)}">${esc(s.is_operational?(s.is_isolated?'ISOLATED':s.health_state.toUpperCase()):'FAILED')}</span></div><div class="fleet-role">${esc(s.role||'UNASSIGNED')}</div><div class="fleet-position">x ${fmt(s.position_x_m/1000,2)} km <span>/</span> y ${fmt(s.position_y_m/1000,2)} km</div><div class="fleet-meter"><div><span>ENERGY INDEX*</span><strong>${fmt(s.energy_percent,1)}%</strong></div><div class="meter-track"><i style="width:${s.energy_percent}%;background:${s.energy_percent<20?colors.red:colors.cyan}"></i></div></div><div class="fleet-chip-row">${s.capabilities.map(c=>`<span class="capability-chip">${esc(c.replaceAll('_',' '))}</span>`).join('')}</div><div class="fleet-card-bottom"><span>Δv ${fmt(s.remaining_dv_mps,3)} m/s</span><span>Sensor +${fmt(s.coverage_contribution?.unique_contribution_pct||0,2)} pp</span></div></article>`).join('');
  $('#fleetTable').innerHTML=sats.map(s=>`<tr><td><strong>${esc(s.satellite_id)}</strong><div class="table-sub status-inline ${statusClass(s.status)}">${esc(s.status)}</div></td><td><span class="role-pill">${esc(s.role||'UNASSIGNED')}</span></td><td class="mono">${fmt(s.position_x_m/1000,2)} / ${fmt(s.position_y_m/1000,2)}</td><td class="mono">${fmt(s.velocity_x_mps,3)} / ${fmt(s.velocity_y_mps,3)}</td><td><div class="table-meter"><i style="width:${s.energy_percent}%"></i><span>${fmt(s.energy_percent,1)}%</span></div></td><td class="mono">${fmt(s.remaining_dv_mps,3)} m/s</td><td>${s.nearest_neighbor_distance_m==null?'—':fmt(s.nearest_neighbor_distance_m/1000,2)+' km'}</td><td>${fmt(s.coverage_contribution?.unique_contribution_pct||0,2)} pp</td><td>${s.anomalies.length?`<span class="status-badge bad">${esc(s.anomalies.join(', '))}</span>`:'<span class="muted">No current flags</span>'}</td></tr>`).join('');
}
function renderFailures(d) {
  const counts={confirmed:d.mission.failed_satellites,suspected:d.mission.suspected_links,isolated:d.communication.isolated_ids.length};
  $('#failureSummaryChips').innerHTML=`<span class="summary-tag">${counts.confirmed} confirmed</span><span class="summary-tag warn">${counts.suspected} suspected links</span><span class="summary-tag">${counts.isolated} isolated</span>`;
  $('#timeoutThreshold').textContent=d.communication.missed_threshold;
  $('#failureSatelliteList').innerHTML=d.satellites.map(s=>`<div class="failure-sat-row"><div class="failure-sat-main"><div class="sat-badge ${statusClass(s.status)}">${esc(s.satellite_id)}</div><div><strong>${esc(s.status)}</strong><small>${s.is_operational?'Operational membership retained':`Failure reason: ${esc(s.failure_reason||'modelled fault')}`}</small></div></div><div class="failure-observers">${s.suspected_by.length?`Suspected by <b>${s.suspected_by.map(esc).join(', ')}</b>`:s.is_isolated?'Temporary communication isolation — not a confirmed bus failure':'No active peer suspicion'}<div class="failure-confidence"><div><span>HEARTBEAT TIMEOUT INDICATOR</span><b>${fmt(s.failure_confidence_pct,0)}%</b><small>${s.missed_heartbeat_periods} missed periods (worst peer) · not a failure probability</small></div><div class="meter-track"><i style="width:${s.failure_confidence_pct}%;background:${s.failure_confidence_pct>=100?colors.red:colors.amber}"></i></div></div></div><div class="failure-row-actions">${s.is_operational?`<button class="small-action" data-comm-toggle="${esc(s.satellite_id)}" data-isolated="${s.is_isolated?'false':'true'}">${s.is_isolated?'Restore link':'Isolate link'}</button>`:''}</div></div>`).join('');
  $('#failureEventTimeline').innerHTML=makeEvents(d.events, false);
  populateSatelliteSelects(d);
}
function populateSatelliteSelects(d) {
  const options=d.satellites.map(s=>`<option value="${esc(s.satellite_id)}">${esc(s.satellite_id)} · ${esc(s.role||'unassigned')} · ${s.is_operational?'operational':'failed'}</option>`).join('');
  ['faultSat','scheduleSat','commSat'].forEach(id=>{const el=$(`#${id}`);const old=el.value;el.innerHTML=options;if([...el.options].some(o=>o.value===old))el.value=old;});
}
function updateFaultParam(kind, id) {
  const el=$(`#${id}`); const wrap=el.closest('label');
  if(kind==='bus_failure'){el.value='';el.placeholder='Not required';el.disabled=true;wrap.classList.add('muted-input');}
  else if(kind==='payload_failure'){el.disabled=false;el.placeholder='observation_highres or observation_wide';if(!['observation_highres','observation_wide'].includes(el.value))el.value='observation_highres';wrap.classList.remove('muted-input');}
  else{el.disabled=false;el.placeholder='0.0 to 1.0';el.value=(Number(el.value)>=0&&Number(el.value)<=1&&el.value!==''?el.value:'0.5');wrap.classList.remove('muted-input');}
}
function renderRoles(d) {
  $('#assignmentState').textContent=d.mission.assignment_feasible?'CAPABILITY-FEASIBLE':'INFEASIBLE / DEGRADED'; $('#assignmentState').className=`summary-tag ${d.mission.assignment_feasible?'':'bad'}`;
  const active=d.satellites.filter(s=>s.is_operational);
  $('#roleAssignments').innerHTML=d.roles.map(r=>`<article class="role-card ${r.critical?'critical':''} ${r.assigned_satellite?'':'vacant'}"><div class="role-card-top"><span class="role-priority">P${r.priority}</span><span class="status-badge ${r.feasible?'good':'bad'}">${r.feasible?'FEASIBLE':'VACANT'}</span></div><strong>${esc(r.role)}</strong><div class="role-assignee">${r.assigned_satellite?`<span class="role-avatar">${esc(r.assigned_satellite)}</span><span>${esc(r.assigned_satellite)} assigned</span>`:'<span class="muted">No valid assignment</span>'}</div><div class="role-card-foot"><span>${r.critical?'MISSION-CRITICAL':'OPTIONAL / DUPLICABLE'}</span><small>target y ${fmt(r.target_y_m/1000,1)} km</small></div><div class="role-capabilities">Requires any: ${r.required_any.map(esc).join(' / ')}</div></article>`).join('');
  $('#assignmentRationale').innerHTML=[
    ['Active candidates',`${active.length} operational satellites`],['Mission policy',active.length===4?'Preserve the four critical roles':active.length===5?'Four critical roles plus one tracker':'Fill all six roles when feasible'],['Unique assignment',new Set(Object.values(Object.fromEntries(d.roles.filter(r=>r.assigned_satellite).map(r=>[r.assigned_satellite,r.role])))).size+' role labels active'],['Capability filter','A satellite cannot receive a role if its required hardware is unavailable'],['Cost terms','Distance + switch + energy + delta-v + ISL quality + maneuverability − marginal coverage reward'],['Failure policy',d.mission.assignment_feasible?'Assignment remains feasible':'Do not report full recovery while role assignment is infeasible']
  ].map(([title,value])=>`<div class="rationale-row"><span>${esc(title)}</span><strong>${esc(value)}</strong></div>`).join('');
  const selected=new Map(d.roles.filter(r=>r.assigned_satellite).map(r=>[`${r.role}|${r.assigned_satellite}`,true]));
  $('#assignmentMatrix').innerHTML=d.assignment_matrix.map(c=>`<tr class="${selected.has(`${c.role}|${c.satellite_id}`)?'selected-row':''}"><td><strong>${esc(c.role)}</strong></td><td>${esc(c.satellite_id)}</td><td><span class="status-badge ${c.eligible?'good':'bad'}">${c.eligible?'YES':'NO'}</span></td><td class="mono">${fmt(c.distance_cost,0)}</td><td class="mono">${fmt(c.switch_penalty,0)}</td><td class="mono">${fmt(c.energy_penalty,0)}</td><td class="mono">${fmt(c.delta_v_penalty,0)}</td><td class="mono">${fmt(c.communication_penalty,0)}</td><td class="mono">${fmt(c.maneuver_penalty,0)}</td><td class="mono">−${fmt(c.coverage_reward,0)}</td><td class="mono">${c.total_cost==null?'—':fmt(c.total_cost,0)}</td><td>${selected.has(`${c.role}|${c.satellite_id}`)?'<span class="selected-tag">SELECTED</span>':'—'}</td></tr>`).join('');
  const assignmentEvents=d.events.filter(e=>e.category==='assignment'); $('#assignmentEvents').innerHTML=makeEvents(assignmentEvents,false);
}
function renderFormation(d) {
  $('#formationDetailChart').innerHTML=makeFormationSvg(d, 900, 500, true); $('#formationStateTag').textContent=d.mission.formation_error_rms_m<=1000?'CONVERGED':d.simulation.time_s===0?'INITIAL STATE':'RECONFIGURING';
  $('#formationRmsDetail').textContent=`${fmt(d.mission.formation_error_rms_m,1)} m`; $('#formationMinDist').textContent=d.mission.min_pair_distance_m==null?'—':fmtKm(d.mission.min_pair_distance_m); $('#formationSafeDist').textContent=fmt(d.config.safe_distance_m,0)+' m';
  const used=d.satellites.reduce((n,s)=>n+Math.max(0,s.initial_delta_v_mps-s.remaining_dv_mps),0); $('#formationDvUsed').textContent=fmt(used,3)+' m/s';
  const saturated=d.satellites.filter(s=>Math.hypot(s.control_x_mps2,s.control_y_mps2)>=.000999).length; $('#formationSaturated').textContent=`${saturated} / ${d.mission.active_satellites}`;
  $('#formationWarning').innerHTML=d.mission.min_pair_distance_m!=null&&d.mission.min_pair_distance_m<d.config.safe_distance_m?`<b>Separation warning</b><span>Minimum pair distance is below the configured threshold. The heuristic response is not a safety proof.</span>`:`<b>Separation monitor</b><span>Minimum current active-pair distance ${d.mission.min_pair_distance_m==null?'not available':fmtKm(d.mission.min_pair_distance_m)}. Monitoring is diagnostic only.</span>`;
  const targets=d.formation.target_positions; $('#formationTable').innerHTML=d.satellites.map(s=>{const t=targets[s.satellite_id];return `<tr><td><strong>${esc(s.satellite_id)}</strong></td><td>${esc(s.role||'—')}</td><td class="mono">${fmt(s.position_x_m/1000,2)} / ${fmt(s.position_y_m/1000,2)}</td><td class="mono">${t?`${fmt(t.x/1000,2)} / ${fmt(t.y/1000,2)}`:'—'}</td><td>${s.formation_error_m==null?'—':fmt(s.formation_error_m,1)+' m'}</td><td class="mono">${fmt(s.control_x_mps2,6)} / ${fmt(s.control_y_mps2,6)}</td><td><span class="status-badge ${s.maneuver_capable?'good':'bad'}">${s.maneuver_capable?'YES':'NO'}</span></td></tr>`}).join('');
  $('#distanceTable').innerHTML=d.formation.pair_distances.map(p=>`<tr><td>${esc(p.a)} ↔ ${esc(p.b)}</td><td class="mono">${fmt(p.distance_m,1)} m</td><td>${fmt(d.config.safe_distance_m,0)} m</td><td><span class="status-badge ${p.safe?'good':'bad'}">${p.safe?'WITHIN LIMIT':'BELOW LIMIT'}</span></td></tr>`).join('');
}
function renderCoverage(d) {
  $('#coverageGridSize').textContent=`${d.config.nx} × ${d.config.ny}`; $('#coverageNowBadge').textContent=`${fmt(d.coverage.coverage_pct,2)}% covered`;
  $('#coverageModelCards').innerHTML=d.coverage.models.map((m,i)=>`<article class="model-kpi"><div class="model-icon">${['▦','◉','⌁','⊙','▤'][i]}</div><div class="model-kpi-title">${esc(m.name)}</div><div class="model-kpi-value">${fmt(m.value_pct,1)}<small>%</small></div><p>${esc(m.description)}</p><div class="meter-track"><i style="width:${Math.max(0,Math.min(100,m.value_pct))}%"></i></div></article>`).join('');
  $('#observationPoints').innerHTML=d.coverage.observation_points.map(p=>`<div class="observation-point"><span class="obs-point-marker ${p.covered?'covered':'uncovered'}">${p.covered?'✓':'!'}</span><div><strong>${esc(p.id)}</strong><small>x 0.0 km · y ${fmt(p.y_m/1000,1)} km</small></div><span class="status-badge ${p.covered?'good':'bad'}">${p.covered?'COVERED':'GAP'}</span></div>`).join('');
  $('#initialCoverageDetail').textContent=pct(d.coverage.initial_coverage_pct,2);$('#currentCoverageDetail').textContent=pct(d.coverage.coverage_pct,2);$('#coverageChangeDetail').textContent=`${d.coverage.coverage_pct-d.coverage.initial_coverage_pct>=0?'+':''}${fmt(d.coverage.coverage_pct-d.coverage.initial_coverage_pct,2)} pp`;$('#coverageTargetDetail').textContent=pct(d.coverage.target_pct,1);
  $('#coverageGapNotice').innerHTML=d.coverage.coverage_pct>=d.coverage.target_pct?`<b>Coverage target met</b><span>${fmt(d.coverage.coverage_pct,2)}% meets the configured ${fmt(d.coverage.target_pct,1)}% threshold.</span>`:`<b>${fmt(d.coverage.uncovered_cells,0)} uncovered grid cells</b><span>Current grid coverage is below the mission target. Role recovery does not necessarily restore baseline sensor geometry.</span>`;
  $('#coverageModelTable').innerHTML=d.coverage.models.map(m=>`<div class="model-row"><div><strong>${esc(m.name)}</strong><small>${esc(m.description)}</small></div><div class="model-row-bar"><i style="width:${Math.max(0,Math.min(100,m.value_pct))}%"></i></div><strong>${fmt(m.value_pct,2)}%</strong></div>`).join('');
  $('#coverageHistoryChartDetail').innerHTML=makeLineSvg(d.metrics.map(x=>({x:x.time_s,y:x.coverage_fraction*100})), 'Coverage (%)');
}
function renderCommunications(d) {
  const c=d.communication; $('#commDelivered').textContent=d.mission.heartbeat_delivered.toLocaleString(); $('#commDropped').textContent=d.mission.heartbeat_dropped.toLocaleString(); $('#commLinks').textContent=c.link_up_count; $('#commSuspicions').textContent=d.mission.suspected_links;
  $('#commNetSummary').textContent=c.connected_fraction>=.999?'CONNECTED MESH':`${c.components.length} NETWORK COMPONENTS`;
  $('#commGraph').innerHTML=makeNetworkSvg(d,760,450); $('#commRangeLabel').textContent=`Configured range ${fmt(d.config.communication_range_m/1000,1)} km`; $('#commComponentsLabel').textContent=`${c.components.length} component${c.components.length===1?'':'s'}`;
  $('#commConfigList').innerHTML=`<div class="metric-row"><span>Heartbeat cadence</span><strong>${fmt(c.heartbeat_period_s,1)} s</strong></div><div class="metric-row"><span>Suspicion threshold</span><strong>${c.missed_threshold} missed periods</strong></div><div class="metric-row"><span>Packet-loss probability</span><strong>${fmt(c.packet_loss_probability*100,1)}%</strong></div><div class="metric-row"><span>Disconnected spacecraft</span><strong>${c.isolated_ids.length}</strong></div>`;
  $('#linkTable').innerHTML=c.links.map(l=>`<tr><td><strong>${esc(l.a)} ↔ ${esc(l.b)}</strong></td><td>${fmt(l.distance_m/1000,2)}</td><td><span class="status-badge ${l.up?'good':'bad'}">${l.up?'UP':'UNAVAILABLE'}</span></td><td><div class="table-meter"><i style="width:${l.quality_pct}%"></i><span>${fmt(l.quality_pct,1)}%</span></div></td><td>${fmt(c.packet_loss_probability*100,1)}% random loss</td></tr>`).join('');
  $('#commEvents').innerHTML=makeEvents(d.events.filter(e=>e.category==='communication'),false);
  const old=$('#commSat').value; $('#commSat').innerHTML=d.satellites.map(s=>`<option value="${esc(s.satellite_id)}">${esc(s.satellite_id)}${s.is_isolated?' · isolated':''}${s.is_operational?'':' · failed'}</option>`).join(''); if([...$('#commSat').options].some(o=>o.value===old))$('#commSat').value=old;
}
function renderRecovery(d) {
  const m=d.mission, passed=m.recovery_checks.filter(c=>c.passed).length, total=m.recovery_checks.length;
  $('#recoveryVerdict').textContent=m.recovery_passed?'RECOVERY VERIFIED':m.status==='DEGRADED'?'MISSION DEGRADED':'RECOVERY INCOMPLETE'; $('#recoveryVerdict').className=`verdict-pill ${m.recovery_passed?'good':m.status==='DEGRADED'?'bad':'warn'}`;
  $('#recoveryBanner').className=`recovery-banner ${m.recovery_passed?'passed':m.status==='DEGRADED'?'degraded':'ongoing'}`;
  $('#recoveryBannerTitle').textContent=m.recovery_passed?'All configured recovery checks passed':m.status==='DEGRADED'?'Swarm cannot meet the current role / survivor policy':m.status==='COVERAGE BELOW TARGET'?'Role recovery did not restore required coverage':'Swarm recovery remains in progress';
  $('#recoveryBannerText').textContent=`${m.active_satellites}/6 operational · ${fmt(m.coverage_pct,2)}% coverage · ${fmt(m.formation_error_rms_m,1)} m RMS formation error · ${fmt(m.connected_fraction*100,0)}% connectivity · ${fmt(m.energy_draw_pct_points,2)} energy-index points used · recovery time ${m.recovery_time_s==null?'not achieved':fmt(m.recovery_time_s,1)+' s'}`;
  $('#recoveryScore').textContent=`${passed}/${total}`;
  $('#recoveryChecks').innerHTML=m.recovery_checks.map((c,i)=>`<div class="check-row"><div class="check-icon ${c.passed?'pass':'fail'}">${c.passed?'✓':'!'}</div><div class="check-copy"><strong>${esc(c.name)}</strong><small>${esc(c.detail)}</small></div><span class="check-result ${c.passed?'good':'bad'}">${c.passed?'PASS':'NOT MET'}</span></div>`).join('');
  const comparison=[['Operational spacecraft',`${6} → ${m.active_satellites}`,m.active_satellites===6],['Coverage',`${fmt(m.initial_coverage_pct,2)}% → ${fmt(m.coverage_pct,2)}%`,m.coverage_pct>=m.initial_coverage_pct],['Connectivity',`${100}% baseline → ${fmt(m.connected_fraction*100,1)}%`,m.connected_fraction>=.999],['Active sensor payloads',`${d.coverage.initial_active_sensors} → ${d.coverage.active_sensors}`,d.coverage.active_sensors>=2],['Critical roles',`${m.critical_roles_restored?'RESTORED':'NOT RESTORED'}`,m.critical_roles_restored],['Configured coverage target',`${fmt(m.coverage_pct,2)}% / ${fmt(m.coverage_target_pct,1)}%`,m.coverage_pct>=m.coverage_target_pct],['Energy index consumed',`${fmt(m.energy_draw_pct_points,2)} percentage points (software proxy)`,m.energy_draw_pct_points<100]];
  $('#recoveryComparison').innerHTML=comparison.map(([label,value,good])=>`<div class="comparison-row"><div><span>${esc(label)}</span><strong>${esc(value)}</strong></div><span class="comparison-state ${good?'good':'bad'}">${good?'▲':'▼'}</span></div>`).join('');
  const steps=[['Initialize six CubeSats',true],['Monitor health & heartbeat',d.simulation.time_s>0],['Detect and confirm failures',m.failed_satellites>0],['Reassign mission roles',m.assignment_feasible&&d.simulation.time_s>0],['Reconfigure formation',d.simulation.time_s>0],['Optimize mission coverage',d.simulation.time_s>0],['Verify recovery',m.recovery_passed]];
  $('#workflowSteps').innerHTML=steps.map((s,i)=>`<div class="workflow-step"><div class="workflow-index ${s[1]?'complete':''}">${s[1]?'✓':String(i+1).padStart(2,'0')}</div><strong>${esc(s[0])}</strong><span class="status-badge ${s[1]?'good':'neutral'}">${s[1]?'DONE':'PENDING'}</span></div>`).join('');
  $('#modelLimits').innerHTML=d.model_limits.map((t,i)=>`<div class="limit-card"><span>${String(i+1).padStart(2,'0')}</span><p>${esc(t)}</p></div>`).join('');
}
function renderSettings(d) {
  if(!state.settingsInitialized){
    const cfg = d.config.satellite_settings || {};
    const initialRows = d.satellites.map(s=>{
      const q=cfg[s.satellite_id]||{}; const z=q.state||d.initial_states[s.satellite_id]||[0,0,0,0];
      const caps=q.capabilities||s.installed_capabilities||[];
      return `<tr data-sat="${esc(s.satellite_id)}"><th scope="row">${esc(s.satellite_id)}<small>${esc(s.role||'Unassigned')}</small></th>
        <td><input aria-label="${esc(s.satellite_id)} initial x in km" data-field="x_km" type="number" step="0.1" value="${fmt(z[0]/1000,3)}"></td>
        <td><input aria-label="${esc(s.satellite_id)} initial y in km" data-field="y_km" type="number" step="0.1" value="${fmt(z[1]/1000,3)}"></td>
        <td><input aria-label="${esc(s.satellite_id)} initial vx in m/s" data-field="vx_mps" type="number" step="0.001" value="${fmt(z[2],3)}"></td>
        <td><input aria-label="${esc(s.satellite_id)} initial vy in m/s" data-field="vy_mps" type="number" step="0.001" value="${fmt(z[3],3)}"></td>
        <td><input aria-label="${esc(s.satellite_id)} initial energy percent" data-field="energy_percent" type="number" min="0" max="100" step="1" value="${fmt(q.energy_percent??100,1)}"></td>
        <td><input aria-label="${esc(s.satellite_id)} delta-v budget" data-field="remaining_dv_mps" type="number" min="0" step="0.1" value="${fmt(q.remaining_dv_mps??10,3)}"></td>
        <td><input aria-label="${esc(s.satellite_id)} max acceleration" data-field="max_acceleration_mps2" type="number" min="0" step="0.0001" value="${Number(q.max_acceleration_mps2??.001)}"></td>
        <td><input aria-label="${esc(s.satellite_id)} installed capabilities" data-field="capabilities" type="text" value="${esc(caps.join(', '))}" title="Comma-separated hardware capabilities"></td></tr>`;
    }).join('');
    $('#initialFormationList').innerHTML=`<div class="table-scroll initial-config-scroll"><table class="init-table"><thead><tr><th>SAT / ORIGINAL ROLE</th><th>X (km)</th><th>Y (km)</th><th>Vx (m/s)</th><th>Vy (m/s)</th><th>ENERGY (%)</th><th>Δv (m/s)</th><th>MAX u (m/s²)</th><th>INSTALLED CAPABILITIES (comma separated)</th></tr></thead><tbody>${initialRows}</tbody></table></div>`;
    $('#rolePriorityList').innerHTML=d.roles.map(r=>`<div class="role-priority-row"><span class="role-priority">P${r.priority}</span><div><strong>${esc(r.role)}</strong><small>${esc(r.required_any.join(' or '))}</small></div><span class="status-badge ${r.critical?'warn':'neutral'}">${r.critical?'CRITICAL':'OPTIONAL'}</span></div>`).join('');
    const form=$('#settingsForm'); const values={...d.config,packet_loss_percent:Number(d.config.packet_loss_probability)*100};
    Object.entries(values).forEach(([k,v])=>{const el=form.elements.namedItem(k);if(el && typeof v!=='object')el.value=v;});
    state.settingsInitialized=true;state.settingsDirty=false;
  }
  $('#settingsFaultSchedule').innerHTML=d.fault_schedule.map(f=>`<div class="scheduled-event"><span class="event-time">T+${fmt(f.time_s,1)}s</span><strong>${esc(f.satellite_id)}</strong><span>${esc(f.kind.replaceAll('_',' '))}${f.value?` · ${esc(f.value)}`:''}</span></div>`).join('')||'<div class="empty-state">No preset faults configured.</div>';
}

function makeEvents(events, compact) {
  if(!events?.length)return '<div class="empty-state">No events recorded yet. Start the simulation to populate the event stream.</div>';
  return events.map(e=>{
    const type=e.type||e.kind||'event',t=Number(e.time_s)||0;const category=e.category||'event';
    const label=type.replaceAll('_',' ');const isBad=/failure|failed|suspected|infeasible|isolated|dropped/i.test(type)||/bus_failure|payload_failure/.test(e.kind||'');
    const isGood=/restored|cleared|reassignment|received|feasible/i.test(type);let detail=e.description||e.reason||e.satellite_id||`${e.observer||''} → ${e.target||''}`;
    if(e.assignment && type==='role_reassignment')detail=Object.entries(e.assignment).map(([sid,role])=>`${sid}: ${role}`).join(' · ');
    return `<div class="event-row ${compact?'compact':''}"><div class="event-timestamp">T+${fmt(t,1)}s</div><div class="event-event-icon ${isBad?'bad':isGood?'good':'neutral'}">${isBad?'!':isGood?'✓':'·'}</div><div class="event-main"><strong>${esc(label)}</strong><small>${esc(detail)}</small></div><span class="event-category">${esc(category)}</span></div>`;
  }).join('');
}
function makeFormationSvg(d,width,height,large) {
  const m={l:60,r:30,t:24,b:45},pw=width-m.l-m.r,ph=height-m.t-m.b;const xmin=-16000,xmax=16000,ymin=-18000,ymax=18000;
  const X=x=>m.l+(x-xmin)/(xmax-xmin)*pw, Y=y=>m.t+(ymax-y)/(ymax-ymin)*ph;
  let g='';for(let x=-15000;x<=15000;x+=5000){g+=`<line x1="${X(x)}" y1="${m.t}" x2="${X(x)}" y2="${m.t+ph}" class="svg-grid"/><text x="${X(x)}" y="${height-18}" text-anchor="middle" class="svg-axis-label">${x/1000}</text>`;}for(let y=-15000;y<=15000;y+=5000){g+=`<line x1="${m.l}" y1="${Y(y)}" x2="${m.l+pw}" y2="${Y(y)}" class="svg-grid"/><text x="${m.l-10}" y="${Y(y)+4}" text-anchor="end" class="svg-axis-label">${y/1000}</text>`;}
  const trails=new Map(d.formation.trajectory.map(t=>[t.time_s,t.positions]));
  d.satellites.forEach(s=>{const pts=d.formation.trajectory.map(t=>t.positions[s.satellite_id]).filter(Boolean).map(p=>`${X(p.x)},${Y(p.y)}`).join(' ');if(pts)g+=`<polyline points="${pts}" fill="none" stroke="${s.is_operational?colors.cyan:colors.red}" stroke-opacity=".32" stroke-width="1.6"/>`;});
  // Close the live formation boundary around operational spacecraft only.
  const active=d.satellites.filter(s=>s.is_operational).sort((a,b)=>Math.atan2(a.position_y_m,a.position_x_m)-Math.atan2(b.position_y_m,b.position_x_m));
  if(active.length>=3){const livePts=active.map(s=>`${X(s.position_x_m)},${Y(s.position_y_m)}`).join(' ');g+=`<polygon points="${livePts}" fill="${colors.cyan}" fill-opacity=".035" stroke="${colors.cyan}" stroke-opacity=".75" stroke-width="1.8" stroke-dasharray="7 6"/>`;}
  const targetEntries=Object.entries(d.formation.target_positions);
  if(targetEntries.length>=3){const targetPts=targetEntries.map(([,t])=>`${X(t.x)},${Y(t.y)}`).join(' ');g+=`<polygon points="${targetPts}" fill="${colors.amber}" fill-opacity=".025" stroke="${colors.amber}" stroke-opacity=".85" stroke-width="1.5" stroke-dasharray="3 5"/>`;}
  Object.entries(d.formation.target_positions).forEach(([sid,t])=>{g+=`<rect x="${X(t.x)-5}" y="${Y(t.y)-5}" width="10" height="10" transform="rotate(45 ${X(t.x)} ${Y(t.y)})" fill="none" stroke="${colors.amber}" stroke-width="1.5"/><text x="${X(t.x)+8}" y="${Y(t.y)-10}" class="svg-target-label">${esc(sid)} target</text>`;});
  d.satellites.forEach(s=>{const x=X(s.position_x_m),y=Y(s.position_y_m);const c=s.is_operational?(s.is_isolated?colors.amber:colors.cyan):colors.red;g+=`<circle cx="${x}" cy="${y}" r="${large?7:6}" fill="${c}" fill-opacity=".16" stroke="${c}" stroke-width="1.5"/><circle cx="${x}" cy="${y}" r="${large?3.5:3}" fill="${c}"/><text x="${x+9}" y="${y-8}" class="svg-node-label">${esc(s.satellite_id)}</text>`;});
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Relative satellite positions and trajectories" class="orbit-svg"><defs><linearGradient id="orbitBg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#101b2e"/><stop offset="1" stop-color="#091322"/></linearGradient></defs><rect width="${width}" height="${height}" rx="8" fill="url(#orbitBg)"/>${g}<line x1="${X(0)}" y1="${m.t}" x2="${X(0)}" y2="${m.t+ph}" class="svg-axis"/><line x1="${m.l}" y1="${Y(0)}" x2="${m.l+pw}" y2="${Y(0)}" class="svg-axis"/><text x="${m.l+pw/2}" y="${height-2}" text-anchor="middle" class="svg-axis-title">Cross-track x (km)</text><text transform="translate(16 ${m.t+ph/2}) rotate(-90)" text-anchor="middle" class="svg-axis-title">Along-track y (km)</text></svg>`;
}
function makeLineSvg(points,label) {
  const w=780,h=250,m={l:48,r:18,t:20,b:34},pw=w-m.l-m.r,ph=h-m.t-m.b; if(!points.length)return '<div class="empty-state">No sampled metric points yet.</div>';
  const xmin=points[0].x,xmax=Math.max(points[points.length-1].x,xmin+1),ymin=0,ymax=100;const X=x=>m.l+(x-xmin)/(xmax-xmin)*pw,Y=y=>m.t+(ymax-y)/(ymax-ymin)*ph;
  let s='';for(let y=0;y<=100;y+=25){s+=`<line x1="${m.l}" y1="${Y(y)}" x2="${w-m.r}" y2="${Y(y)}" class="svg-grid"/><text x="${m.l-9}" y="${Y(y)+4}" text-anchor="end" class="svg-axis-label">${y}</text>`;}
  for(let i=0;i<=4;i++){const x=xmin+(xmax-xmin)*i/4;s+=`<text x="${X(x)}" y="${h-10}" text-anchor="middle" class="svg-axis-label">${fmt(x,0)}s</text>`;}
  const line=points.map(p=>`${X(p.x)},${Y(Math.max(0,Math.min(100,p.y)))}`).join(' ');const area=`${X(points[0].x)},${Y(0)} ${line} ${X(points[points.length-1].x)},${Y(0)}`;
  return `<svg viewBox="0 0 ${w} ${h}" class="history-svg" role="img" aria-label="${esc(label)} over simulation time"><defs><linearGradient id="areaFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${colors.cyan}" stop-opacity=".22"/><stop offset="1" stop-color="${colors.cyan}" stop-opacity="0"/></linearGradient></defs>${s}<polygon points="${area}" fill="url(#areaFill)"/><polyline points="${line}" fill="none" stroke="${colors.cyan}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>${points.length<120?points.map(p=>`<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="2" fill="${colors.cyan}"/>`).join(''):''}</svg>`;
}
function makeNetworkSvg(d,width,height) {
  const m={l:55,r:55,t:40,b:40},positions={S1:[.15,.52],S2:[.28,.23],S3:[.42,.48],S4:[.58,.48],S5:[.72,.23],S6:[.85,.52]};const pts={};Object.entries(positions).forEach(([id,xy])=>pts[id]=[m.l+xy[0]*(width-m.l-m.r),m.t+xy[1]*(height-m.t-m.b)]);
  let svg='';d.communication.links.forEach(l=>{const a=pts[l.a],b=pts[l.b];svg+=`<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="${l.up?colors.cyan:'#42516a'}" stroke-width="${l.up?1.8:1.1}" stroke-opacity="${l.up?.65:.5}" ${l.up?'':'stroke-dasharray="5 5"'}/>`;});
  d.satellites.forEach(s=>{const [x,y]=pts[s.satellite_id],c=s.is_operational?(s.is_isolated?colors.amber:colors.cyan):colors.red;svg+=`<circle cx="${x}" cy="${y}" r="25" fill="${c}" fill-opacity=".1" stroke="${c}" stroke-width="1.5"/><circle cx="${x}" cy="${y}" r="4" fill="${c}"/><text x="${x}" y="${y+4}" text-anchor="middle" class="network-node-id">${esc(s.satellite_id)}</text><text x="${x}" y="${y+43}" text-anchor="middle" class="network-node-status">${s.is_operational?(s.is_isolated?'ISOLATED':'ONLINE'):'FAILED'}</text>`;});
  return `<svg viewBox="0 0 ${width} ${height}" class="orbit-svg" role="img" aria-label="Current inter-satellite communication graph"><rect width="${width}" height="${height}" rx="8" fill="#0b1524"/><text x="${width/2}" y="24" text-anchor="middle" class="svg-axis-title">ACTIVE SPACECRAFT LINKS · TOPOLOGY VIEW</text>${svg}<text x="${width/2}" y="${height-10}" text-anchor="middle" class="svg-axis-label">Node spacing is schematic · link status uses distance and capability rules</text></svg>`;
}
function drawCoverageMaps() {
  if(!state.grid||!state.data)return;
  drawCoverageCanvas($('#overviewCoverageCanvas'),state.grid,state.data,0.6);
  drawCoverageCanvas($('#detailCoverageCanvas'),state.grid,state.data,1.0);
}
function drawCoverageCanvas(canvas, grid, d, scaleFactor) {
  if(!canvas || !grid?.mask?.length)return;
  const ctx=canvas.getContext('2d');const w=canvas.width,h=canvas.height;ctx.clearRect(0,0,w,h);ctx.fillStyle='#08111f';ctx.fillRect(0,0,w,h);
  const pl=42,pr=18,pt=18,pb=38,pw=w-pl-pr,ph=h-pt-pb;const nx=grid.x_axis_m.length,ny=grid.y_axis_m.length;const cw=pw/nx,ch=ph/ny;
  for(let r=0;r<ny;r++){const yy=pt+ph-(r+1)*ch;for(let c=0;c<nx;c++){ctx.fillStyle=grid.mask[r][c]?'rgba(73, 200, 176, .6)':'rgba(40, 56, 78, .8)';ctx.fillRect(pl+c*cw,yy,Math.ceil(cw+.25),Math.ceil(ch+.25));}}
  const xMap=x=>pl+(x-grid.x_axis_m[0])/(grid.x_axis_m[nx-1]-grid.x_axis_m[0])*pw;const yMap=y=>pt+ph-(y-grid.y_axis_m[0])/(grid.y_axis_m[ny-1]-grid.y_axis_m[0])*ph;
  // Footprints are clipped to the mapped plot rectangle.
  ctx.save();ctx.beginPath();ctx.rect(pl,pt,pw,ph);ctx.clip();
  for(const s of d.satellites){if(!s.is_operational||!s.capabilities.some(c=>c.startsWith('observation_')))continue;const radius=grid.sensor_radius_m;const rpx=radius/(grid.x_axis_m[nx-1]-grid.x_axis_m[0])*pw;const rpy=radius/(grid.y_axis_m[ny-1]-grid.y_axis_m[0])*ph;ctx.beginPath();ctx.ellipse(xMap(s.position_x_m),yMap(s.position_y_m),rpx,rpy,0,0,Math.PI*2);ctx.fillStyle='rgba(131,169,255,.07)';ctx.fill();ctx.strokeStyle='rgba(131,169,255,.75)';ctx.lineWidth=1.2;ctx.stroke();}
  for(const p of d.coverage.observation_points||[]){ctx.beginPath();ctx.arc(xMap(p.x_m),yMap(p.y_m),4,0,Math.PI*2);ctx.fillStyle=p.covered?colors.green:colors.red;ctx.fill();ctx.strokeStyle='#08111f';ctx.lineWidth=1;ctx.stroke();}
  ctx.restore();ctx.strokeStyle='#435571';ctx.lineWidth=1;ctx.strokeRect(pl,pt,pw,ph);
  ctx.fillStyle='#92a6c4';ctx.font='11px ui-monospace, monospace';ctx.textAlign='center';for(let x=-2000;x<=2000;x+=1000){const xx=xMap(x);ctx.fillText(`${(x/1000).toFixed(0)}`,xx,h-19);ctx.strokeStyle='rgba(105,128,162,.23)';ctx.beginPath();ctx.moveTo(xx,pt+ph);ctx.lineTo(xx,pt+ph+4);ctx.stroke();}
  ctx.textAlign='right';for(let y=-15000;y<=15000;y+=5000){const yy=yMap(y);ctx.fillText(`${(y/1000).toFixed(0)}`,pl-7,yy+4);}
  ctx.textAlign='center';ctx.fillText('Cross-track x (km)',pl+pw/2,h-4);ctx.save();ctx.translate(12,pt+ph/2);ctx.rotate(-Math.PI/2);ctx.fillText('Along-track y (km)',0,0);ctx.restore();
}

async function loadGrid(force=false) {
  if(!state.data)return;if(!force && state.grid && state.data.simulation.time_s-state.gridTime<.75)return;
  try {const g=await api('/api/coverage-grid');state.grid=g;state.gridTime=state.data.simulation.time_s;drawCoverageMaps();}catch(e){console.warn('Coverage grid refresh failed',e);}
}
async function poll() {
  if(state.polling)return;state.polling=true;
  try {const tickAt=performance.now();if(state.data?.simulation.running){const elapsed=state.lastTickAt===null?0.25:Math.min(2,Math.max(0,(tickAt-state.lastTickAt)/1000));await api('/api/tick',{seconds:elapsed*Number($('#liveSpeed').value||state.data.simulation.speed)});}const d=await api('/api/state');state.lastTickAt=d.simulation.running?performance.now():null;render(d);if(!state.grid || d.simulation.time_s-state.gridTime>=1 || state.currentView==='coverage'&&d.simulation.running)await loadGrid(true);}catch(e){$('#runStatusText').textContent='API ERROR';console.error(e);}finally{state.polling=false;}
}
async function action(url,body={}) { try{await api(url,body);await poll();}catch(e){toast(e.message,'error');} }

$$('.nav-item').forEach(btn=>btn.addEventListener('click',()=>setView(btn.dataset.view)));
$$('[data-go]').forEach(btn=>btn.addEventListener('click',()=>setView(btn.dataset.go)));
$('#startBtn').addEventListener('click',()=>action(state.data?.simulation.time_s>0?'/api/resume':'/api/start'));
$('#pauseBtn').addEventListener('click',()=>action('/api/pause'));
$('#liveSpeed').addEventListener('change',async e=>{const speed=Number(e.target.value);try{await api('/api/speed',{speed});const speedField=$('#settingsForm').elements.namedItem('speed');if(speedField)speedField.value=speed;await poll();toast(`Playback speed set to ${speed}×`, 'success');}catch(err){toast(err.message,'error');await poll();}});
$('#resetBtn').addEventListener('click',()=>$('#modalOverlay').classList.remove('hidden'));
$('#cancelResetBtn').addEventListener('click',()=>$('#modalOverlay').classList.add('hidden'));
$('#confirmResetBtn').addEventListener('click',async()=>{ $('#modalOverlay').classList.add('hidden'); const form=$('#settingsForm'); if(form){try{const config=readSettings();await api('/api/reset',{config,fault_schedule:state.data?.fault_schedule?.length?state.data.fault_schedule.map(f=>({time_s:f.time_s,satellite_id:f.satellite_id,kind:f.kind,value:f.value})):undefined});state.grid=null;state.settingsInitialized=false;await poll();toast('Simulation reset to initial conditions','success');}catch(e){toast(e.message,'error');}} });
$('#faultKind').addEventListener('change',e=>updateFaultParam(e.target.value,'faultValue'));$('#scheduleKind').addEventListener('change',e=>updateFaultParam(e.target.value,'scheduleValue'));
$('#faultNowForm').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.currentTarget);let value=f.get('value');const kind=f.get('kind');if(kind==='bus_failure')value=null;else if(kind==='thruster_degradation')value=Number(value);try{await api('/api/fault',{satellite_id:f.get('satellite_id'),kind,value});$('#faultFormMessage').textContent='Fault applied to current simulation state.';toast('Fault injection applied','warning');await poll();}catch(err){$('#faultFormMessage').textContent=err.message;toast(err.message,'error');}});
$('#faultScheduleForm').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.currentTarget);let value=f.get('value');const kind=f.get('kind');if(kind==='bus_failure')value=null;else if(kind==='thruster_degradation')value=Number(value);try{await api('/api/schedule-fault',{satellite_id:f.get('satellite_id'),offset_s:Number(f.get('offset_s')),kind,value});$('#faultFormMessage').textContent='Fault added to the simulation event queue.';toast('Scheduled fault added','success');await poll();}catch(err){$('#faultFormMessage').textContent=err.message;toast(err.message,'error');}});
$('#randomOneFaultBtn').addEventListener('click',async()=>{try{await api('/api/random-failures',{count:1});$('#faultFormMessage').textContent='Seeded random bus failure injected. Any pending scheduled faults were cleared to keep this test scenario isolated.';await poll();toast('Random single-spacecraft failure injected','warning');}catch(e){toast(e.message,'error');}});
$('#randomTwoFaultBtn').addEventListener('click',async()=>{try{await api('/api/random-failures',{count:2});$('#faultFormMessage').textContent='Two seeded random bus failures injected. Any pending scheduled faults were cleared to keep this test scenario isolated.';await poll();toast('Random two-spacecraft failure scenario injected','warning');}catch(e){toast(e.message,'error');}});
$('#commForm').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.currentTarget);try{await api('/api/comm',{satellite_id:f.get('satellite_id'),isolated:f.get('isolated')==='true'});$('#commFormMessage').textContent='Communication state updated. Spacecraft hardware state is unchanged.';toast('Communication state updated','success');await poll();}catch(err){$('#commFormMessage').textContent=err.message;toast(err.message,'error');}});
document.addEventListener('click',async e=>{const btn=e.target.closest('[data-comm-toggle]');if(!btn)return;try{await api('/api/comm',{satellite_id:btn.dataset.commToggle,isolated:btn.dataset.isolated==='true'});await poll();toast(`${btn.dataset.commToggle} communication state updated`,'success');}catch(err){toast(err.message,'error');}});
function defaultSatelliteSettings(){return {S1:{state:[0,-12500,0,0],capabilities:['compute','isl'],energy_percent:100,remaining_dv_mps:10,max_acceleration_mps2:.001},S2:{state:[0,-7500,0,0],capabilities:['compute','isl','observation_highres'],energy_percent:100,remaining_dv_mps:10,max_acceleration_mps2:.001},S3:{state:[0,-2500,0,0],capabilities:['compute','isl','observation_highres'],energy_percent:100,remaining_dv_mps:10,max_acceleration_mps2:.001},S4:{state:[0,2500,0,0],capabilities:['compute','isl','observation_wide'],energy_percent:100,remaining_dv_mps:10,max_acceleration_mps2:.001},S5:{state:[0,7500,0,0],capabilities:['compute','isl','observation_wide'],energy_percent:100,remaining_dv_mps:10,max_acceleration_mps2:.001},S6:{state:[0,12500,0,0],capabilities:['compute','isl','enhanced_relay'],energy_percent:100,remaining_dv_mps:10,max_acceleration_mps2:.001}};}
function readInitialSettings(){const output={};$$('#initialFormationList tr[data-sat]').forEach(row=>{const get=k=>row.querySelector(`[data-field="${k}"]`).value;output[row.dataset.sat]={state:[Number(get('x_km'))*1000,Number(get('y_km'))*1000,Number(get('vx_mps')),Number(get('vy_mps'))],energy_percent:Number(get('energy_percent')),remaining_dv_mps:Number(get('remaining_dv_mps')),max_acceleration_mps2:Number(get('max_acceleration_mps2')),capabilities:get('capabilities').split(',').map(x=>x.trim()).filter(Boolean)};});return output;}
function readSettings(){const f=$('#settingsForm');const v=Object.fromEntries(new FormData(f).entries());return {duration_s:Number(v.duration_s),dt_s:Number(v.dt_s),speed:Number(v.speed),packet_loss_probability:Number(v.packet_loss_percent)/100,heartbeat_period_s:Number(v.heartbeat_period_s),missed_heartbeat_threshold:Number(v.missed_heartbeat_threshold),sensor_radius_m:Number(v.sensor_radius_m),communication_range_m:Number(v.communication_range_m),safe_distance_m:Number(v.safe_distance_m),coverage_target_pct:Number(v.coverage_target_pct),seed:Number(v.seed),nx:41,ny:151,satellite_settings:readInitialSettings()};}
$('#settingsForm').addEventListener('input',()=>{state.settingsDirty=true;});
$('#initialFormationList').addEventListener('input',()=>{state.settingsDirty=true;});
$('#settingsForm').addEventListener('submit',async e=>{e.preventDefault();try{const config=readSettings();await api('/api/reset',{config,fault_schedule:[{time_s:20,satellite_id:'S2',kind:'bus_failure',value:null},{time_s:40,satellite_id:'S5',kind:'bus_failure',value:null}]});state.grid=null;state.settingsDirty=false;state.settingsInitialized=false;await poll();toast('Configuration applied. Mission reset to t=0.','success');$('#settingsMessage').textContent='Configuration applied. Simulation reset to initial conditions with the default two-fault scenario.';}catch(err){$('#settingsMessage').textContent=err.message;toast(err.message,'error');}});
$('#restoreDefaultsBtn').addEventListener('click',async()=>{try{const defaults={duration_s:120,dt_s:.2,speed:5,packet_loss_percent:0,heartbeat_period_s:1,missed_heartbeat_threshold:3,sensor_radius_m:8000,communication_range_m:18000,safe_distance_m:250,coverage_target_pct:75,seed:7};Object.entries(defaults).forEach(([k,v])=>$('#settingsForm').elements.namedItem(k).value=v);const config={...readSettings(),satellite_settings:defaultSatelliteSettings()};await api('/api/reset',{config,fault_schedule:[{time_s:20,satellite_id:'S2',kind:'bus_failure',value:null},{time_s:40,satellite_id:'S5',kind:'bus_failure',value:null}]});state.grid=null;state.settingsDirty=false;state.settingsInitialized=false;await poll();toast('Default configuration restored','success');}catch(err){toast(err.message,'error');}});

poll();setInterval(poll,250);
