#!/usr/bin/env bash
# Runs GraphHopper for Pathfinder Run pinned to JDK 17, regardless of whatever
# `java` resolves to on this machine (config-ontario.yml requires Java 17+;
# do not rely on system default).
#
# Takes an optional config file as the first argument (default:
# config-ontario.yml) -- e.g. ./run-graphhopper.sh config-ontario.yml
#
# -Xmx10g: found the hard way, back when this was accurate -- this
# machine's default JVM heap ergonomic is exactly 4GB (confirmed via
# -XX:+PrintFlagsFinal), and importing the full-Ontario graph with the
# real (province-wide) greenspace.geojson loaded once pushed live heap
# usage as high as ~9.3GB during subnetwork marking; launched without an
# explicit -Xmx right after discovering that, and it would have OOM'd
# partway through the (then ~8-minute) import.
#
# That specific finding is now stale, not current justification --
# greenness/in_greenspace (the LIVE per-query custom_areas version) was
# removed at province scale (see pathfinder_foot.json's and
# config-ontario.yml's comments; the disambiguating LM test that led to
# its removal), so a from-scratch import no longer approaches anywhere
# near that ~9.3GB peak. Greenness itself came back later via a
# completely different, cheap mechanism (graphhopper-ext/'s static
# encoded value, baked in once at import time, not evaluated live) --
# a full-Ontario import with it included measured at 55s, matching the
# no-greenness baseline (~62s), nothing like the old live-lookup
# approach.
#
# IMPORTANT operational trap: this script runs the STOCK jar, which
# imports from scratch (no greenspace encoded value) if graph-cache
# doesn't already exist at the configured location -- it does NOT know
# about graphhopper-ext at all. Since pathfinder_foot.json now references
# "greenspace" unconditionally, running this script against a missing/
# deleted graph-cache directory will fail at startup (the custom_model
# references an encoded value the stock-imported graph doesn't have).
# If graph-cache-ontario/ doesn't exist, use run-import.sh instead (see
# graphhopper-ext/README.md) -- NOT this script -- to build it with
# greenness included, then this script serves it normally afterward.
# See deploy/pathfinder-graphhopper.service's own comment for the
# current, measured, steady-state-serving number that replaced this
# reasoning for the production systemd unit (1536m there, not 10g --
# production never imports at all, so even the stale ~9.3GB import-time
# figure was never the right number for that file).
#
# Left at 10g here anyway, deliberately, not re-tuned down: this is a
# ceiling, not a reservation, so it costs nothing on this dev machine's
# actual RAM either way, and this script is what a local from-scratch
# reimport still runs through occasionally -- keeping a generous ceiling
# here means one less thing to re-check if greenness (or some other
# import-time-heavy mechanism) ever comes back per the filed future item.
# Applied universally here (not just for the Ontario config) for the same
# reason -- costs nothing for a small graph that never approaches it, and
# there's only ever one GraphHopper instance running at a time
# post-Ontario-migration anyway.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

CONFIG_FILE="${1:-config-ontario.yml}"
if [ $# -gt 0 ]; then
  shift
fi

LINKED_JDK="/Library/Java/JavaVirtualMachines/openjdk-17.jdk/Contents/Home"
BREW_JDK="$(brew --prefix openjdk@17 2>/dev/null || true)/libexec/openjdk.jdk/Contents/Home"

if [ -x "$LINKED_JDK/bin/java" ]; then
  JAVA_HOME="$LINKED_JDK"
elif [ -x "$BREW_JDK/bin/java" ]; then
  JAVA_HOME="$BREW_JDK"
else
  echo "error: openjdk@17 not found (checked $LINKED_JDK and $BREW_JDK)." >&2
  echo "Install it with: brew install openjdk@17" >&2
  exit 1
fi
export JAVA_HOME

echo "Using JAVA_HOME=$JAVA_HOME"
"$JAVA_HOME/bin/java" -version

exec "$JAVA_HOME/bin/java" -Xmx10g -jar graphhopper-web.jar server "$CONFIG_FILE" "$@"
