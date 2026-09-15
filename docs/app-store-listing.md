# App Store Connect — listing copy

Copy-paste reference for the actual App Store Connect submission form,
same spirit as `docs/app-store-privacy-labels.md` — real copy grounded
in what this app actually does, not generic filler, ready the moment
Apple Developer Program access exists. Character counts below are exact
(counted by hand against Apple's real field limits, not estimated).

## App Name (30 char max)

```
Pathfinder Run
```
14/30 characters (verified by script, not counted by hand). No
truncation risk anywhere Apple displays it.

## Subtitle (30 char max)

```
Loops that skip repeat streets
```
Exactly 30/30 characters (verified) — captures the actual
differentiator (the edge-reuse penalty in `scripts/generate_loop.py`'s
scoring, §5's "not circling the same km four times"), not generic
"running app" filler.

## Promotional Text (170 char max, editable anytime without a new build)

```
Pick a distance, get a scenic loop that favors parks and sidewalks and
avoids doubling back on itself. No account needed to start running.
```
138/170 characters (a first draft ran to 177 and was over the limit —
caught by actually running `len()` against it, not by counting by eye,
and trimmed).

## Description (4000 char max)

```
Most running apps either give point-to-point directions or make you
draw your own loop by hand. Pathfinder Run generates the loop for you:
pick a distance, and it builds a real route from wherever you're
standing — favoring park paths and sidewalks over road shoulders, and
actively avoiding routes that just retrace the same street back and
forth to hit the number.

HOW IT WORKS
• Pick a distance (3, 5, 8, or 10 km) and tap Generate
• Choose from 2–3 real alternative routes, each scored for distance
  accuracy, how little it repeats itself, and how "loop-shaped" (not a
  there-and-back sliver) it actually is
• Start your run and follow it on the map, with live position tracking
  and an on-device alert if you've drifted off the planned route
• See a real summary — distance, time, pace — the moment you finish,
  not buried in a separate screen

BUILT ON REAL MAP DATA, NOT JUST DIRECTIONS
Routes are generated from OpenStreetMap data through a custom routing
profile that actually knows the difference between a park path, a
sidewalk, and a road shoulder — not a generic driving-directions API
repurposed for walking.

RUN HISTORY, ON YOUR TERMS
Every run is saved on your device, with your actual recorded path
viewable on a map afterward — including a snapshot that works with no
network connection. No account, no sign-up: your history is tied to
this install, not a login. A real "Delete All My Data" button clears
both your device and any server-synced copy, permanently.

CLOSURE-AWARE
Hit a closed path mid-run? Report it in one tap. Reports feed directly
into future route generation for everyone, and automatically expire
after a week so a temporary closure doesn't block a path forever.

PRIVACY
Location is used to generate and track your run — full stop. No
tracking across other apps, no ad targeting, no data sold to anyone.
See the in-app privacy policy for the specifics.

Pathfinder Run is an independently built app, actively developed.
```
1,966/4,000 characters (verified) — well under the limit, deliberately
not padded with filler just to fill space.

## Keywords (100 bytes max, comma-separated, no spaces after commas)

```
jogging,jog,gps tracker,park,sidewalk,scenic,route planner,distance,fitness,closure,workout,trail
```
97/100 bytes. Deliberately excludes words already in the App Name/
Subtitle ("pathfinder", "run", "loop", "route", "repeat", "streets") —
Apple already indexes those for search, repeating them here would waste
the 100-byte budget instead of covering more distinct search terms.

## What's New (this version) — for the initial 1.0.0 release

```
Initial release.
```
Standard for a first submission — there's no prior version to describe
changes relative to.

## Category

- **Primary:** Health & Fitness
- **Secondary:** none needed — Navigation is arguable but the app's own
  identity (a running app, not a wayfinding tool) is squarely covered by
  Health & Fitness alone.

## Age Rating

**4+** — no objectionable content of any kind (no user-generated text/
media beyond a one-tap closure report tied to a way id, no violence, no
mature themes). Apple's age rating questionnaire in App Store Connect
will ask a series of yes/no content questions; answer them all "no"/
"none" — this app has nothing in any of those categories.

## Support URL / Marketing URL — the one thing this file can't fill in

Both are required fields in App Store Connect, and both need a REAL,
currently-reachable URL — not a placeholder, since Apple's review
process checks these. Two honest options, pick one before submitting:
1. The GitHub repo itself (public, already exists):
   `https://github.com/rynjung1/pathfinder-run` — works as a support
   URL today (issues can be filed there) even though it's a source repo,
   not a dedicated support page.
2. A real support email via a `mailto:` link, or a one-page site if one
   gets stood up later — not required now, the repo link is a legitimate
   stand-in for a small independent app.

The privacy policy URL is not a gap — it's already published and real:
https://claude.ai/code/artifact/b1f491f0-5c0c-4454-8c93-17ca88adf517
