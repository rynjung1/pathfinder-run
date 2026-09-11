#!/usr/bin/env bash
# Runs GraphHopper for Pathfinder Run pinned to JDK 17, regardless of whatever
# `java` resolves to on this machine (config.yml requires Java 17+; do not
# rely on system default).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

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

exec "$JAVA_HOME/bin/java" -jar graphhopper-web.jar server config.yml "$@"
