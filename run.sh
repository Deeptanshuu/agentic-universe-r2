#!/usr/bin/env bash
# Starts the controller and runs it until the scenario finishes. No Docker, no extra services:
# Python 3.11+ with httpx (requirements.txt). State goes to ./state/<scenario>.sqlite (override with
# CONTROLLER_STATE_DIR). Re-running the same command after a crash resumes from that file.
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
cd "$(dirname "$0")"
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python; else PY=python3; fi
fi
exec "$PY" -m controller run --url "$SIM_URL"
