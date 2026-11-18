#!/bin/sh
# Copies common/ (the shared modules, policy and gateway client) into every pattern's src/agent so each
# template deploys on its own. Edit common/ only, then run this. `sh sync-common.sh --check` fails when a copy drifted.
set -eu
cd "$(dirname "$0")"
mode="${1:-copy}"
status=0
for target in patterns/handoff/src/agent patterns/group-chat/src/agent patterns/single-agent patterns/reflection patterns/sequential-pipeline; do
  [ -d "$target" ] || continue
  if [ "$mode" = "--check" ]; then
    if ! diff -rq -x '__pycache__' common/app "$target/app" >/dev/null 2>&1 \
       || ! diff -q common/gateway_client.py "$target/gateway_client.py" >/dev/null 2>&1 \
       || ! diff -rq common/config "$target/config" >/dev/null 2>&1; then
      echo "DRIFT: $target differs from common/"; status=1
    fi
  else
    rm -rf "$target/app" "$target/config"
    cp -r common/app "$target/app"
    cp -r common/config "$target/config"
    cp common/gateway_client.py "$target/gateway_client.py"
    find "$target" -name __pycache__ -type d -prune -exec rm -rf {} +
    echo "synced $target"
  fi
done
[ "$mode" = "--check" ] && [ "$status" = 0 ] && echo "OK: every copy matches common/"
exit "$status"
