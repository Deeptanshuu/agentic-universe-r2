"""Kill/restart integration test against a real simulator (slow: ~4 min). Opt in with RUN_SLOW=1:

    RUN_SLOW=1 python -m pytest -q tests/test_crash_recovery.py
"""
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.skipif(os.environ.get("RUN_SLOW") != "1", reason="slow integration test; set RUN_SLOW=1")
def test_kill_restart_keeps_invariants():
    base = os.path.join(REPO, "runs", "base02")
    if not os.path.isdir(base):
        subprocess.run([sys.executable, "-m", "sim", "baseline", "--scenario", "scenarios/public_02_incidents.yaml",
                        "--run-dir", "runs/base02"], cwd=REPO, check=True)
    r = subprocess.run([sys.executable, "scripts/crash_test.py", "--kills", "6", "--seed", "5", "--port", "8791",
                        "--run", "crash_pytest", "--out", "runs/crash_pytest.md"], cwd=REPO)
    assert r.returncode == 0  # the script exits non-zero if any invariant fails
