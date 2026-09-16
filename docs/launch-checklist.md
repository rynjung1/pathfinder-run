# Launch checklist — the actual order to do things in

Everything needed to submit Pathfinder Run to the App Store already
exists somewhere in this repo — this file's only job is putting it in
the right ORDER, since `README.md`'s "App Store readiness" section lists
facts, not a sequence. Followed the dependency chain originally decided
on (Apple Developer Program first, VPS after) — moot now that step 1
turned out to already be satisfied (see below), but VPS/domain (step 4)
is genuinely unblocked and ready to start now.

## 1. ~~Apple Developer Program enrollment~~ — already done

Turned out this Apple ID is already the Account Holder of an active
membership (discovered 2026-09-16 when a fresh enroll attempt was
rejected with "Your Apple Account is already associated with the
Account Holder of a membership") — no multi-week wait, no $99 payment,
no ID verification needed. The only action required was accepting an
updated Program License Agreement (a banner at developer.apple.com/account)
to regain working access to Certificates/IDs/Profiles and App Store
Connect.

If this is ever stale (a future renewal lapses, or a different Apple ID
is used), the original guidance was: developer.apple.com/programs/enroll,
real legal name/address matching ID, $99/year, respond fast to any photo
ID verification request (2026 reports showed 2–7+ week waits common for
a genuinely new enrollment).

## 2. Already done — nothing left to prep here

Checked as of this session: privacy policy published (with a real
contact email), Privacy Manifest, export compliance, delete-my-data,
App Store listing copy, Data Safety/App Privacy answers, and two real
1320×2868 screenshots all exist and are ready. See:
- [`docs/app-store-listing.md`](app-store-listing.md) — name, subtitle,
  description, keywords, category, age rating
- [`docs/app-store-privacy-labels.md`](app-store-privacy-labels.md) —
  the exact App Privacy / Data Safety form answers
- [`docs/app-store-screenshots/`](app-store-screenshots/) — two real
  screenshots (light + dark), correctly sized

## 3. Right now — App Store Connect setup

1. Confirm access at appstoreconnect.apple.com — create the app record
   there (bundle id `com.rynjung.pathfinderrun`, matching
   `mobile/app.json` exactly).
2. Fill in the App Privacy section using
   [`docs/app-store-privacy-labels.md`](app-store-privacy-labels.md).
3. Fill in the listing (name/subtitle/description/etc.) using
   [`docs/app-store-listing.md`](app-store-listing.md). Support URL:
   that file recommends the public GitHub repo as an honest stand-in
   until a dedicated page exists.
4. Paste the privacy policy URL into App Store Connect's privacy policy
   field: https://claude.ai/code/artifact/b1f491f0-5c0c-4454-8c93-17ca88adf517
   — fill in the real contact email there too if it's ever changed.

## 4. VPS + domain — also unblocked now, can run in parallel with #3

Full plan already written and ready to execute, not just planned — see
[`deploy/README.md`](../deploy/README.md) for the complete sequence
(sizing, systemd units, the actual install steps). Short version:

1. Provision a small VPS (deploy/README.md recommends a 4GB-RAM tier —
   sizing is backed by real measurements, not a guess) and register a
   domain (needed for HTTPS — Let's Encrypt can't issue a cert for a
   bare IP).
2. Point the domain's DNS A record at the VPS.
3. Follow `deploy/README.md`'s install steps 1–9 in order — they're
   written as literal commands to run, not something to figure out.
4. Confirm it's actually live:
   `curl https://your-domain/health` should return
   `{"status": "ok"}` (or `{"status": "degraded", ...}` if GraphHopper
   isn't reachable yet — the `/health` endpoint genuinely checks this,
   not just "the process started").

## 5. Point the mobile app at the real backend

One env var, no code change needed (this was a real bug earlier this
session — `API_BASE_URL` used to be hardcoded; it isn't anymore):

```bash
cd mobile
eas env:set --name EXPO_PUBLIC_API_BASE_URL --value https://your-domain --environment production
```

## 6. Build and submit

```bash
cd mobile
eas build --platform ios --profile production
eas submit --platform ios --profile production   # prompts for App Store Connect credentials the first time
```

`mobile/eas.json`'s `production` profile already has `autoIncrement`
set, so build numbers don't need manual bumping between submissions.

## 7. Submit for review

In App Store Connect: attach the build from step 6, attach the two
screenshots from `docs/app-store-screenshots/` (or a fuller set if
more exist by then), confirm the age rating (4+) and category (Health
& Fitness) from `docs/app-store-listing.md`, and submit.

---

Android/Play Store follows the same shape once there's appetite for it
— `mobile/app.json`'s Android config, permissions, and identity are
already correct (see README's App Store readiness section), the same
`eas build --platform android` / `eas submit --platform android`
commands apply, and Play Console's Data Safety form uses the exact same
answers as `docs/app-store-privacy-labels.md`'s "Google Play Console"
section — just not sequenced here since iOS is the stated priority.
