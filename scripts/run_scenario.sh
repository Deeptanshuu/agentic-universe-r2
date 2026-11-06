#!/usr/bin/env bash
# End-to-end: fresh simulator + fresh controller state -> run.sh -> grade -> stop simulator.
#   scripts/run_scenario.sh scenarios/public_02_incidents.yaml mine02 base02 8702 reports/public_02.md
set -euo pipefail
cd "$(dirname "$0")/.."
SCEN="$1"; RUN="$2"; BASE="$3"; PORT="${4:-8700}"; OUT="${5:-}"
PY=.venv/bin/python; [[ -x $PY ]] || PY=python3
export CONTROLLER_STATE_DIR="${CONTROLLER_STATE_DIR:-state/$RUN}"
rm -rf "$CONTROLLER_STATE_DIR"
if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null; then echo "port $PORT already serving a simulator; stop it first" >&2; exit 1; fi
[[ -d runs/$BASE ]] || $PY -m sim baseline --scenario "$SCEN" --run-dir "runs/$BASE"
$PY -m sim serve --scenario "$SCEN" --run-dir "runs/$RUN" --port "$PORT" --force > "runs/$RUN.sim.log" 2>&1 &
SIM=$!
trap 'kill $SIM 2>/dev/null || true' EXIT
for i in $(seq 1 120); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 1; done
START=$(date +%s)
./run.sh "http://127.0.0.1:$PORT"
echo "controller wall time: $(( $(date +%s) - START ))s"
if [[ -n "$OUT" ]]; then
  mkdir -p "$(dirname "$OUT")"
  $PY -m sim grade --run "runs/$RUN" --baseline "runs/$BASE" --out "$OUT" > /dev/null
  sed -n '1,40p' "$OUT"
fi
