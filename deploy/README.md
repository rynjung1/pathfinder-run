# Deployment notes

Not run yet -- provisioning (step 4) is on hold pending a domain (Caddy's
automatic HTTPS needs one; it can't issue a cert for a bare IP). This
documents the assumed layout the two systemd units in this directory
reference, so step 4 is "follow this," not "figure this out."

## Assumed layout on the VPS

```
/opt/pathfinder-run/          # this repo, checked out
/opt/pathfinder-run/venv/     # python3 -m venv venv; pip install -r scripts/requirements.txt
/opt/pathfinder-run/scripts/.env      # real secrets -- copy from scripts/.env.example, fill in
/opt/pathfinder-run/graphhopper/graph-cache-ontario/    # NOT built on the VPS -- see below
```

Runs as a dedicated non-root user (`pathfinder` in the unit files --
`sudo useradd --system --create-home pathfinder` or equivalent), not root
and not your own login user.

## Don't build the graph on the VPS

The full-Ontario import takes a few minutes and modest peak memory --
fine on a dev machine, wasteful to provision a small production VPS
around a one-time cost it only pays during a rebuild. Build locally (as
this whole project has done throughout), then ship the result:

```
rsync -avz graphhopper/graph-cache-ontario/ vps:/opt/pathfinder-run/graphhopper/graph-cache-ontario/
```

`graphhopper-web.jar`, `pathfinder_foot.json`, `config-ontario.yml`,
`data/long_ways_ontario.json`, and `data/boundaries/ontario.geojson` are
all in the repo already (`long_ways_ontario.json` and `ontario.geojson`
are small and git-tracked; `graph-cache-ontario/` is gitignored precisely
because of its size -- that's what the rsync above is for).

(`data/greenspace-ontario/` and the greenness routing rule it fed are no
longer part of the pipeline -- removed at province scale as the confirmed
root cause of /route being unusably slow in flexible mode. See
pathfinder_foot.json's and config-ontario.yml's comments. Nothing to ship
for it currently; that data stays local, unused, pending the future
Java-side fix.)

## Install steps (once a domain is sorted and step 4 resumes)

1. Clone the repo to `/opt/pathfinder-run`, create the `pathfinder` user.
2. `python3 -m venv venv && venv/bin/pip install -r scripts/requirements.txt`
3. `cp scripts/.env.example scripts/.env`, fill in a real
   `PATHFINDER_API_KEY` (`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`).
4. rsync the graph-cache (above) instead of importing on-box.
5. `sudo cp deploy/pathfinder-*.service /etc/systemd/system/ && sudo systemctl daemon-reload`
6. `sudo systemctl enable --now pathfinder-graphhopper`, wait for it to
   report healthy (`curl localhost:8995/health`) before starting the API.
7. `sudo systemctl enable --now pathfinder-route-api`
8. Firewall: allow only 22 (SSH) and 443 (HTTPS, once Caddy is in front)
   inbound. Nothing else -- GraphHopper (8995/8996) and route_api.py
   (127.0.0.1:5001) are both intentionally not internet-reachable
   directly; Caddy is the only public surface.
9. Caddy + the real domain -- still blocked on step 4's prerequisite.

## Backups

Only `scripts/closures.db` needs one -- everything else here is a build
artifact, reproducible from this repo plus the raw OSM extract (see the
hardening review). A simple daily cron is enough at this write volume:

```
0 3 * * * sqlite3 /opt/pathfinder-run/scripts/closures.db ".backup /opt/pathfinder-run/backups/closures-$(date +\%F).db"
```

with a second job pruning anything older than, say, 14 days. Not set up
yet -- this is the concrete command for whenever step 4 resumes.
