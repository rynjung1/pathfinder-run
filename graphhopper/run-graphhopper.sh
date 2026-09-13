#!/usr/bin/env bash
# Runs GraphHopper for Pathfinder Run pinned to JDK 17, regardless of whatever
# `java` resolves to on this machine (config-ontario.yml requires Java 17+;
# do not rely on system default).
#
# Takes an optional config file as the first argument (default:
# config-ontario.yml) -- e.g. ./run-graphhopper.sh config-ontario.yml
#
# -Xmx10g: found the hard way -- this machine's default JVM heap ergonomic
# is exactly 4GB (confirmed via -XX:+PrintFlagsFinal), but importing the
# full-Ontario graph with the real (province-wide) greenspace.geojson
# loaded pushed live heap usage as high as ~9.3GB during subnetwork
# marking. Launched without an explicit -Xmx once, right after this was
# discovered, and it would have OOM'd partway through an 8-minute import.
# Applied universally here (not just for the Ontario config) since it's
# just a ceiling, not a reservation -- costs nothing for a small graph
# that never approaches it, and there's only ever one GraphHopper
# instance running at a time post-Ontario-migration anyway.
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
