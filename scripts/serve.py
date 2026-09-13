#!/usr/bin/env python3
"""
Pathfinder Run -- production entrypoint for route_api.py (pre-deployment
hardening, step 3).

Replaces Flask's built-in dev server for real traffic -- route_api.py's
own `python3 route_api.py` path calls Flask's app.run(), which is
explicitly documented as unfit for production (single-threaded by
default, no hardening) and prints its own warning to that effect on every
startup. This file is the alternative: waitress, a pure-Python
production-grade WSGI server.

Waitress over gunicorn specifically: gunicorn doesn't run on Windows and
has had rougher macOS support historically; waitress is genuinely
cross-platform, so this exact command is testable in local dev (this
machine, macOS) before it's ever deployed (a Linux VPS) -- no "works on
my machine, breaks in prod" gap from the server choice itself.

Binds to 127.0.0.1 by default, NOT 0.0.0.0 -- deliberately different from
route_api.py's own dev entrypoint. In production this process sits behind
a reverse proxy (Caddy, once that's provisioned) that's the only thing
actually facing the internet; nothing else should reach this port
directly. route_api.py's dev entrypoint still defaults to 0.0.0.0
unchanged -- that default exists specifically so an iOS Simulator or a
physical device on the same LAN can reach it during local development,
and this file doesn't touch that.

Usage:
    python3 scripts/serve.py
    PATHFINDER_PORT=8080 python3 scripts/serve.py
See deploy/pathfinder-route-api.service for how this runs under systemd.
"""
import os

from waitress import serve

from route_api import app

HOST = os.environ.get("PATHFINDER_HOST", "127.0.0.1")
PORT = int(os.environ.get("PATHFINDER_PORT", "5001"))
THREADS = int(os.environ.get("PATHFINDER_THREADS", "4"))  # ad hoc, revisit under real load

if __name__ == "__main__":
    print(f"Serving route_api on {HOST}:{PORT} via waitress ({THREADS} threads)")
    serve(app, host=HOST, port=PORT, threads=THREADS)
