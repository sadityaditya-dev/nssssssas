"""Launch the browser-based CubeSat swarm dashboard."""
from __future__ import annotations
import argparse
from cubesat_swarm.dashboard.server import serve


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1", help="Bind address (default: loopback only)")
    p.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    p.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    args = p.parse_args()
    serve(args.host, args.port, not args.no_browser)

if __name__ == "__main__":
    main()
