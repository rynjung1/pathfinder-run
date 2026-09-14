#!/usr/bin/env bash
# Rebuilds the GraphHopper graph-cache WITH greenness included, via
# graphhopper-ext/'s custom importer -- see that directory's README for
# the full mechanism. Does NOT start a server; once this finishes,
# ./run-graphhopper.sh serves the result exactly as before, unchanged
# (the custom code only ever runs at import time, never at serve time --
# confirmed via decompiling graphhopper-web.jar, see graphhopper-ext/README.md).
#
# Takes an optional config file as the first argument (default:
# config-ontario.yml) and an optional way-ids file as the second
# (default: ../data/greenspace_way_ids_ontario.json) -- e.g.
#   ./run-import.sh config-ontario.yml ../data/greenspace_way_ids_ontario.json
#
# JDK-17 detection mirrors run-graphhopper.sh exactly, not duplicated by
# coincidence -- same requirement (config-ontario.yml needs Java 17+),
# same machine, deliberately kept in sync rather than each drifting its
# own copy. -Xmx10g for the same "costs nothing on this dev machine,
# comfortable ceiling" reasoning as that script; a from-scratch import
# with greenness included measured at 55s / well under that ceiling, not
# the old ~9.3GB live-lookup peak.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

CONFIG_FILE="${1:-config-ontario.yml}"
WAY_IDS_FILE="${2:-../data/greenspace_way_ids_ontario.json}"

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

EXT_JAR="../graphhopper-ext/target/graphhopper-ext-1.0.0.jar"
if [ ! -f "$EXT_JAR" ]; then
  echo "error: $EXT_JAR not found -- build it first: (cd ../graphhopper-ext && mvn package)" >&2
  exit 1
fi

echo "Using JAVA_HOME=$JAVA_HOME"
echo "Importing $CONFIG_FILE with greenness from $WAY_IDS_FILE..."

exec "$JAVA_HOME/bin/java" -Xmx10g \
  -cp "graphhopper-web.jar:$EXT_JAR" \
  run.pathfinder.graphhopper.PathfinderImporter \
  "$CONFIG_FILE" "$WAY_IDS_FILE"
