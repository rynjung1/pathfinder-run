# Deployment notes

**Live as of 2026-09-18**: `https://api.pathfinderrun.com` (Hetzner
CX23, Helsinki -- Oracle Cloud's free-tier Ampere capacity was tried
first and abandoned after a real, sustained "out of capacity" error
across multiple retries and hours, not a one-off). Verified end-to-end
against the real deployment, not just "the process started": a real
POST /route over HTTPS returns real generated candidates, and `/health`
reports `{"status": "ok"}` (which itself only reports ok when it can
actually reach GraphHopper, per that endpoint's own design -- see
`scripts/route_api.py`).

The install steps below are the exact commands actually run to get
there, kept here for the next time this needs to be redone (a VPS
rebuild, a second environment, disaster recovery) -- not a plan
anymore, a record.

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

## Install steps (as actually run)

1. Clone the repo to `/opt/pathfinder-run`, create the `pathfinder` user.
2. `python3 -m venv venv && venv/bin/pip install -r scripts/requirements.txt`
3. `cp scripts/.env.example scripts/.env`, fill in a real
   `PATHFINDER_API_KEY` (`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`).
4. rsync the graph-cache (above) instead of importing on-box.
5. `sudo cp deploy/pathfinder-*.service /etc/systemd/system/ && sudo systemctl daemon-reload`
6. `sudo systemctl enable --now pathfinder-graphhopper`, wait for it to
   report healthy (`curl localhost:8995/health`) before starting the API.
7. `sudo systemctl enable --now pathfinder-route-api`, then `curl
   localhost:5001/health` -- this checks GraphHopper connectivity too
   (503 + `{"status": "degraded", ...}` if it can't reach it), not just
   "the process started," so it's worth checking again any time
   GraphHopper restarts independently of this service.
8. Firewall (`ufw`): allow 22 (SSH), 80, and 443 inbound. 80 is not a
   mistake -- Caddy needs it for the ACME HTTP challenge and to redirect
   plain HTTP to HTTPS (see `deploy/Caddyfile`'s own header comment).
   Nothing else -- GraphHopper (8995/8996) and route_api.py
   (127.0.0.1:5001) are both intentionally not internet-reachable
   directly; Caddy is the only public surface.
9. Caddy: install it (see `deploy/Caddyfile`'s header comment for the
   exact apt commands), fill in the real domain in place of that file's
   `api.example.com` placeholder, `sudo cp deploy/Caddyfile
   /etc/caddy/Caddyfile && sudo systemctl reload caddy`. Real gotcha hit
   doing this: Caddy's Debian package does NOT create
   `/var/log/caddy/` itself, and the site block's `log { output file
   ... }` directive fails closed (the whole reload fails, not just
   logging) if that directory doesn't exist with the right owner --
   `sudo mkdir -p /var/log/caddy && sudo chown caddy:caddy
   /var/log/caddy` before the reload, not after hitting the error.

## Backups

**Set up and verified working** (`/etc/cron.d/pathfinder-backups`):

```
0 3 * * * pathfinder sqlite3 /opt/pathfinder-run/scripts/closures.db ".backup /opt/pathfinder-run/backups/closures-$(date +\%F).db"
30 3 * * * root find /opt/pathfinder-run/backups -name 'closures-*.db' -mtime +14 -delete
```

Only `scripts/closures.db` -- `runs.db` deliberately doesn't get one:
the server-side run-history sync is explicitly a best-effort backup
layer on top of each device's own local (authoritative) copy, per
`mobile/db.js`/`scripts/runs.py`'s own documented design -- losing the
server's `runs.db` loses nothing a user doesn't already have on their
own phone. `closures.db` is the one table with no other copy anywhere,
which is what actually makes it worth backing up. Tested directly
(not just trusted to work at 3am): ran the exact backup command by
hand, confirmed the output file is a real, readable SQLite database
with the right schema.

## Server hardening (done beyond the base install steps)

- **SSH password authentication disabled** (`/etc/ssh/sshd_config.d/
  99-pathfinder-hardening.conf`, `PasswordAuthentication no`) -- root
  login already required a key (`prohibit-password`, the Ubuntu cloud
  image default), but password auth was still enabled system-wide,
  meaning the internet's constant background SSH brute-force noise was
  actually being given a login prompt to try against. Verified key-based
  access still works before considering this done, not after.
- **fail2ban** installed and running (its default `sshd` jail is enough
  here -- no other services listen on a public port except Caddy, which
  isn't a fail2ban-relevant login surface).
- **Automatic security updates**: already on by default on this Ubuntu
  cloud image (`unattended-upgrades`, confirmed enabled, not assumed) --
  nothing to add.
- **Caddy access log rotation** (`/etc/logrotate.d/caddy-pathfinder`,
  daily, 14 rotations, compressed): Caddy's Debian package does not set
  this up itself -- `/var/log/caddy/pathfinder-run-access.log` would
  otherwise grow completely unbounded forever. Validated with
  `logrotate -d` (dry run) before trusting it, not just written and
  assumed correct.
- **systemd journal size cap** (`SystemMaxUse=500M` in
  `/etc/systemd/journald.conf`) -- no cap existed before; harmless today
  (33GB free, 68MB of journal so far) but cheap insurance against
  unbounded growth over the app's actual lifetime.
