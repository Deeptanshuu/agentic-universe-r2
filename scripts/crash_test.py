"""Crash-recovery evidence (C13): SIGKILL the controller at random moments, restart it, check it finishes
correctly. Writes reports/crash_recovery.md.

    .venv/bin/python scripts/crash_test.py --scenario scenarios/public_02_incidents.yaml --kills 8

Invariants checked against the simulator's own records (HTTP) and our store:
  1. the scenario finished;
  2. no approval was applied twice and no proposal_id appears twice in the config history;
  3. every approval request the simulator knows about is in our store with the same approval_id (none lost);
  4. no proposal is left in a non-terminal write-ahead state (intent / applying);
  5. every controller-made config version in the simulator is recorded as applied in our store;
  6. every event was ingested exactly once (sum of ingested events == simulator's last event id);
  7. the controller really was restarted (starts == kills + 1).
"""
import argparse
import json
import os
import random
import signal
import sqlite3
import subprocess
import sys
import time

import httpx

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, ".venv", "bin", "python") if os.path.exists(os.path.join(REPO, ".venv")) else sys.executable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="scenarios/public_02_incidents.yaml")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--kills", type=int, default=8)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--run", default="crash02")
    ap.add_argument("--baseline", default="base02")
    ap.add_argument("--out", default="reports/crash_recovery.md")
    a = ap.parse_args()
    os.chdir(REPO)
    rng = random.Random(a.seed)
    state_dir = f"state/{a.run}"
    subprocess.run(["rm", "-rf", state_dir])
    url = f"http://127.0.0.1:{a.port}"
    sim = subprocess.Popen([PY, "-m", "sim", "serve", "--scenario", a.scenario, "--run-dir", f"runs/{a.run}",
                            "--port", str(a.port), "--force"], stdout=open(f"runs/{a.run}.sim.log", "w"),
                           stderr=subprocess.STDOUT)
    c = httpx.Client(base_url=url, timeout=60)
    for _ in range(600):
        try:
            c.get("/health")
            break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    env = dict(os.environ, CONTROLLER_STATE_DIR=state_dir)
    log = []
    t0 = time.time()
    try:
        for k in range(a.kills):
            p = subprocess.Popen(["./run.sh", url], env=env, stdout=subprocess.DEVNULL,
                                 stderr=open(f"{state_dir}.ctl.log", "a") if os.path.isdir(state_dir) else subprocess.DEVNULL)
            # first kill lands inside history ingestion; later ones anywhere in the run
            wait = rng.uniform(4, 9) if k == 0 else rng.uniform(2, 12)
            time.sleep(wait)
            if p.poll() is not None:
                log.append({"kill": k + 1, "note": "controller had already finished"})
                break
            st = c.get("/sim/state").json()
            os.kill(p.pid, signal.SIGKILL)
            p.wait()
            log.append({"kill": k + 1, "after_s": round(wait, 1), "sim_ts": st["ts"], "sim_minute": st["sim_minute"]})
            print("killed", log[-1], flush=True)
        p = subprocess.run(["./run.sh", url], env=env, stdout=subprocess.DEVNULL,
                           stderr=open(f"{state_dir}.ctl.log", "a"))
        wall = time.time() - t0
        res = check(c, state_dir, a, log)
    finally:
        sim.send_signal(signal.SIGTERM)
        sim.wait()
    grade = subprocess.run([PY, "-m", "sim", "grade", "--run", f"runs/{a.run}", "--baseline", f"runs/{a.baseline}",
                            "--out", f"reports/{a.run}_grade.md"], capture_output=True, text=True)
    write(a, log, res, wall)
    print(json.dumps(res, indent=1))
    sys.exit(0 if all(r["ok"] for r in res.values()) else 1)


def check(c, state_dir, a, log):
    st = c.get("/sim/state").json()
    db = sqlite3.connect(f"file:{state_dir}/{st['scenario']}.sqlite?mode=ro", uri=True)
    res = {}
    res["1 finished"] = {"ok": bool(st["finished"]), "detail": st["ts"]}
    wfs = [w["workflow_id"] for w in c.get("/workflows").json()]
    dup_a, dup_p, ctrl_versions = [], [], []
    for wf in wfs:
        hist = c.get(f"/workflows/{wf}/config/history").json()
        aids = [h["approval_id"] for h in hist if h.get("approval_id")]
        pids = [h["proposal_id"] for h in hist if h.get("proposal_id")]
        dup_a += [x for x in set(aids) if aids.count(x) > 1]
        dup_p += [x for x in set(pids) if pids.count(x) > 1]
        ctrl_versions += [(wf, h["version"], h["proposal_id"]) for h in hist if h["actor"].startswith("controller")]
    res["2 no double apply"] = {"ok": not dup_a and not dup_p, "detail": {"dup_approvals": dup_a, "dup_proposals": dup_p,
                                                                         "controller_versions": len(ctrl_versions)}}
    sim_apprs = c.get("/approvals").json()
    ours = dict(db.execute("SELECT approval_id, proposal_id FROM proposals WHERE approval_id IS NOT NULL").fetchall())
    lost = [x["approval_id"] for x in sim_apprs if ours.get(x["approval_id"]) != x["proposal_id"]]
    res["3 no lost approval"] = {"ok": not lost, "detail": {"sim_approvals": len(sim_apprs), "lost": lost}}
    open_rows = db.execute("SELECT proposal_id, status FROM proposals WHERE status IN ('intent','applying')").fetchall()
    res["4 no dangling write-ahead rows"] = {"ok": not open_rows, "detail": open_rows}
    applied = {r[0] for r in db.execute("SELECT proposal_id FROM proposals WHERE status='applied'")}
    missing = [v for v in ctrl_versions if v[2] not in applied]
    res["5 every applied version recorded"] = {"ok": not missing, "detail": {"missing": missing}}
    ev = db.execute("SELECT SUM(events), COUNT(*) FROM chunks").fetchone()
    res["6 events ingested exactly once"] = {"ok": ev[0] == st["last_event_id"],
                                             "detail": {"ingested": ev[0], "sim_last_event_id": st["last_event_id"],
                                                        "chunks": ev[1]}}
    starts = json.loads(db.execute("SELECT v FROM meta WHERE k='starts'").fetchone()[0])
    kills = len([x for x in log if "sim_minute" in x])
    res["7 restarted after every kill"] = {"ok": starts == kills + 1, "detail": {"starts": starts, "kills": kills}}
    return res


def write(a, log, res, wall):
    lines = [f"# Crash-recovery test — {a.scenario}", "",
             f"Command: `.venv/bin/python scripts/crash_test.py --scenario {a.scenario} --kills {a.kills} "
             f"--seed {a.seed}`", "",
             "The controller was SIGKILLed (no shutdown hooks) at random wall-clock moments — the first one inside "
             "the history ingestion — and restarted with the same `./run.sh` each time. Wall time "
             f"{wall:.0f}s.", "", "## Kills", "", "| # | after (s) | sim time at kill |", "|---|---|---|"]
    for k in log:
        lines.append(f"| {k['kill']} | {k.get('after_s', '-')} | {k.get('sim_ts', k.get('note'))} |")
    lines += ["", "## Invariants", "", "| check | result | detail |", "|---|---|---|"]
    for name, r in res.items():
        lines.append(f"| {name} | {'PASS' if r['ok'] else 'FAIL'} | `{json.dumps(r['detail'], default=str)[:300]}` |")
    lines += ["", f"Grade of the interrupted run: `reports/{a.run}_grade.md`."]
    with open(a.out, "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
