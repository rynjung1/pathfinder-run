# Launch checklist — the actual order to do things in

Everything needed to submit Pathfinder Run to the App Store already
exists somewhere in this repo — this file's only job is putting it in
the right ORDER, since `README.md`'s "App Store readiness" section lists
facts, not a sequence. Follows the dependency chain you've already
decided on: Apple Developer Program first, VPS after, since there's no
point paying for/managing a server before knowing the app can actually
ship.

## 1. Right now — start Apple Developer Program enrollment

The long pole, not something either of us can speed up. Apple's own
guidance says 24–48 hours; real 2026 reports from other developers show
waits of 2–7+ weeks are common, sometimes longer, especially if a name/
address mismatch triggers manual ID verification. Every day this isn't
started is a day added to however long the queue actually is.

- Go to developer.apple.com/programs/enroll, sign in with an Apple ID
  that has 2FA enabled
- Use your real legal name (exactly as on ID) and a real street address
  (no P.O. box)
- Pay the $99/year fee
- If Apple asks for photo ID verification, respond immediately — this
  is the single biggest lever on how long the wait actually is

Nothing else in this checklist is blocked on this finishing — only the
final build/submit steps (5+) are. Everything below it can happen while
you wait.

## 2. While waiting — nothing left to prep, this is genuinely done

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

## 3. Once Apple Developer Program is approved

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

## 4. VPS + domain (your stated next step after #1)

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
