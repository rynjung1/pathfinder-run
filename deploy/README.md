# Deployment notes

Not run yet -- provisioning (step 4) is on hold pending a domain (Caddy's
automatic HTTPS needs one; it can't issue a cert for a bare IP). This
documents the assumed layout the two systemd units in this directory
reference, so step 4 is "follow this," not "figure this out."

## VPS sizing

A small VPS (e.g. Hetzner's CX22, 4GB RAM total) is recommended --
confirmed against a real measurement, not a guess. The recommended
budget:

- GraphHopper: 1536MB heap (`-Xmx1536m` in `pathfinder-graphhopper.service`
  -- see that file's comment for the full measurement writeup and
  methodology). Measured cold-loading the existing graph-cache (no
  reimport, matching exactly how the VPS runs it) and serving real
  routing traffic: ~600-700MB under sustained normal load, with real
  headroom confirmed directly against up to 40 concurrent requests
  (1.42-1.55GB peak, zero OOM) -- well beyond the concurrency an
  early-beta running app should see.
- route_api.py (waitress, a handful of threads) + Caddy + the OS/systemd
  itself: a few hundred MB combined, comfortably.

That leaves real slack on a 4GB box, not a tight fit -- the 4GB tier is
fine. (The previous `-Xmx10g` in the systemd unit was a bug, not a
sizing decision: it was carried over from this machine's *import-time*
peak with the since-removed greenness data loaded, a scenario the VPS
never hits at all since it's designed to rsync a pre-built graph-cache
rather than ever import on-box -- see "Don't build the graph on the VPS"
below. 10GB doesn't fit in 4GB regardless of what it was measuring.)

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

The full-Ontario import takes about a minute and modest peak memory --
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

Greenness (§5 point 1's second bullet) IS part of the pipeline again, as
of `graphhopper-ext/`'s static "greenspace" encoded value -- see that
directory's README for the full mechanism. This means a rebuild is no
longer just `run-graphhopper.sh`: greenness needs a fresh
`data/greenspace_way_ids_ontario.json` (regenerate via
`scripts/compute_greenspace_way_ids.py` whenever the OSM extract itself
changes -- not needed for a routine rebuild against the same extract)
and the actual import must go through `graphhopper-ext`'s
`PathfinderImporter`, not the stock jar's own import path (see that
README for the exact command). Once the graph-cache exists, *serving*
it is unchanged -- still the plain, unmodified `graphhopper-web.jar`
via `run-graphhopper.sh`; the custom code only ever runs at import time.

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
