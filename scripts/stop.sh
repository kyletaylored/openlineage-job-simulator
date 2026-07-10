#!/usr/bin/env bash
# Find and stop any running instance of this demo app.
#
# The auto-port-selection feature (see app/web.py) means a leftover instance
# doesn't fail loudly -- the next launch just silently bumps to the next
# port instead, so instances quietly pile up if you don't Ctrl+C them.
# This scans a range of ports for listeners whose command line matches
# this app and kills them.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Precedence: explicit APP_PORT env var > .env's APP_PORT > default 8080.
# (Getting this backwards is how a "test on a scratch port" invocation ends
# up killing the real running instance instead -- ask me how I know.)
if [ -n "${APP_PORT:-}" ]; then
  BASE_PORT="$APP_PORT"
elif [ -f .env ] && grep -qE '^APP_PORT=' .env; then
  BASE_PORT="$(grep -E '^APP_PORT=' .env | tail -1 | cut -d= -f2)"
else
  BASE_PORT=8080
fi
RANGE_END=$((BASE_PORT + 30))

found=0
for port in $(seq "$BASE_PORT" "$RANGE_END"); do
  pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  [ -z "$pid" ] && continue

  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  case "$cmd" in
    *app.py*)
      echo "Stopping PID $pid (port $port): $cmd"
      kill "$pid" 2>/dev/null || true
      found=$((found + 1))
      ;;
    *)
      echo "Skipping PID $pid (port $port): does not look like this app ($cmd)"
      ;;
  esac
done

if [ "$found" -eq 0 ]; then
  echo "No running instance found on ports $BASE_PORT-$RANGE_END."
else
  echo "Stopped $found instance(s)."
fi
