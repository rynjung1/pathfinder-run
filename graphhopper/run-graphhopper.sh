#!/usr/bin/env bash
# Runs GraphHopper for Pathfinder Run pinned to JDK 17, regardless of whatever
# `java` resolves to on this machine (config.yml requires Java 17+; do not
# rely on system default).
#
# Takes an optional config file as the first argument (default: config.yml),
# so the same script runs either city's instance -- e.g.
#   ./run-graphhopper.sh                 # Waterloo Region, port 8989
#   ./run-graphhopper.sh config-guelph.yml   # Guelph, port 8991
# Both can run at once (see config-guelph.yml's header for what actually
# differs between the two configs -- ports, data paths, log files, nothing
# else).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

CONFIG_FILE="${1:-config.yml}"
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

exec "$JAVA_HOME/bin/java" -jar graphhopper-web.jar server "$CONFIG_FILE" "$@"
