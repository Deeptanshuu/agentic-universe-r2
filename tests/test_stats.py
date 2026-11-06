"""Statistics the controller's decisions rest on. Run: python -m pytest -q tests/test_stats.py"""
import json
import math

import numpy as np

from controller import settings as S
from controller.analysis import arm_data
from controller.experiments import Experiments, Scope
from controller.stats import stratified_diff, tv_distance, wilson_lower
from controller.store import Store
from controller.wf import WF, step_towards


def test_stratified_diff_avoids_simpsons_paradox():
    # Day 1: base rate 0.3, A got 10% of traffic. Day 2: base rate 0.6, A got 90%.
    # A == B inside each day, but pooled counts make A look +0.24 better.
    strata = [(30, 100, 270, 900), (540, 900, 60, 100)]
    pooled = (30 + 540) / 1000 - (270 + 60) / 1000
    assert pooled > 0.2
    c = stratified_diff(strata)
    assert abs(c.diff) < 1e-9 and abs(c.z) < 1e-6


def test_wilson_lower_separates_safe_from_unsafe_prompts():
    assert wilson_lower(397, 400) >= 0.97      # ~99% pass rate: acceptable
    assert wilson_lower(390, 400) < 0.97       # ~97.5%: not proven safe at n=400


def test_step_towards_respects_max_shift():
    live, target = {"D1": 0.5, "D2": 0.5}, {"D1": 1.0}
    step = step_towards(live, target, 0.19)
    assert tv_distance(live, step) <= 0.19 + 1e-3
    assert step_towards(live, target, 0.6) == target


def _simulate_fwer(z_win, n_sims=4000, seed=7):
    """A/A test of the interim rule exactly as configured: 1 incumbent at 40%, 3 challengers at 20%,
    ~1080 connected calls/day (nw_renewal scale), two looks a day for 4 days, MIN_N per arm, then the final
    look with the Holm threshold. Returns (P(any false winner), P(lean switch at final look))."""
    rng = np.random.default_rng(seed)
    p = 0.55
    shares = np.array([0.4, 0.2, 0.2, 0.2])
    per_look = 540
    looks = 8
    false_win = lean = 0
    for _ in range(n_sims):
        s = np.zeros(4)
        n = np.zeros(4)
        won = False
        for k in range(looks):
            add = rng.multinomial(per_look, shares)
            s += rng.binomial(add, p)
            n += add
            r = s / n
            se = np.sqrt(r[0] * (1 - r[0]) / n[0] + r[1:] * (1 - r[1:]) / n[1:])
            z = (r[1:] - r[0]) / se
            ok = (n[1:] >= S.MIN_N["engagement_rate"]) & (n[0] >= S.MIN_N["engagement_rate"])
            if np.any(ok & (z >= z_win)):
                won = True
                break
        if won:
            false_win += 1
            continue
        if z.max() >= S.z_final(3):
            false_win += 1
        elif z.max() >= S.Z_LEAN:
            lean += 1
    return false_win / n_sims, lean / n_sims


def test_false_winner_rate_is_controlled():
    fwer, lean = _simulate_fwer(S.Z_WIN)
    # Under the null the strict rule (interim z>=2.6 + final Holm) declares a false winner ~5-7% of the time
    # per dimension; the 'lean' rule then switches to an EQUAL arm in a further share of runs (harmless
    # by construction under the null; the cost shows up only when arms differ by less than the MDE).
    print(f"FWER={fwer:.3f} lean_switch={lean:.3f}")
    assert fwer < 0.09
    looser, _ = _simulate_fwer(1.96, n_sims=1500)
    assert looser > fwer  # naive z>=1.96 at every look would be much worse


class _Ctl:
    def __init__(self, store, wf):
        self.store = store
        self.wfs = {wf.id: wf}


def _seed(store, wf, rates, n=900):
    rows = []
    for hb in range(10, 34):  # one day of hourly buckets
        for arm, (share, rate) in rates.items():
            k = int(n * share / 24)
            rows.append((wf, "voice", "all", hb, 2, "default", arm, k * 3, k, int(round(k * rate))))
    store.db.executemany("INSERT INTO arm_stats(wf,dim,seg,hb,ver,cohort,arm,n,conn,eng) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)


def test_winner_selection_is_deterministic(tmp_path):
    spec = {"workflow_id": "w", "options": {"voice": ["V1", "V2", "V3", "V4"], "welcome_message": ["W1"]},
            "objective": {"primary": "engagement_rate", "guardrail": {"pickup_rate_max_drop": 0.02}},
            "governance": {"approver_hours": [10, 19]}, "dialling_window": [9, 21], "mid_flight": "allowed"}
    wf = WF("w", spec)
    st = Store(str(tmp_path / "s.sqlite"))
    _seed(st, "w", {"V1": (0.4, 0.50), "V2": (0.2, 0.50), "V3": (0.2, 0.62), "V4": (0.2, 0.61)}, n=5000)
    exp = Experiments(_Ctl(st, wf))
    exp._logging = False
    row = ("w", "default", "voice", "explore", "V1", "V1", "[]", 0, 0, "[]", None)
    cfg = {"allocations": {"default": {"voice": {"V1": 0.4, "V2": 0.2, "V3": 0.2, "V4": 0.2}}}, "cohorts": []}
    outs = [exp.evaluate(wf, Scope(row), cfg, 40) for _ in range(3)]
    assert all(json.dumps(o, sort_keys=True) == json.dumps(outs[0], sort_keys=True) for o in outs)
    t = outs[0][0]
    assert t["mode"] == "exploit" and t["champion"] in ("V3", "V4")
    # the pick is the challenger with the higher lower confidence bound, not a coin flip
    data = arm_data(st, "w", "voice", "all", 0)
    lcb = {a: data.cmp(a, "V1", "engagement_rate").lcb() for a in ("V3", "V4")}
    assert t["champion"] == max(lcb, key=lambda a: (round(lcb[a], 6), a))


def test_no_winner_without_enough_data(tmp_path):
    spec = {"workflow_id": "w", "options": {"voice": ["V1", "V2"]},
            "objective": {"primary": "engagement_rate", "guardrail": {"pickup_rate_max_drop": 0.02}},
            "governance": {"approver_hours": [10, 19]}, "dialling_window": [9, 21], "mid_flight": "allowed"}
    wf = WF("w", spec)
    st = Store(str(tmp_path / "s.sqlite"))
    _seed(st, "w", {"V1": (0.5, 0.40), "V2": (0.5, 0.80)}, n=200)  # huge effect, tiny sample
    exp = Experiments(_Ctl(st, wf))
    exp._logging = False
    row = ("w", "default", "voice", "explore", "V1", "V1", "[]", 0, 0, "[]", None)
    cfg = {"allocations": {"default": {"voice": {"V1": 0.5, "V2": 0.5}}}, "cohorts": []}
    assert exp.evaluate(wf, Scope(row), cfg, 40) == []  # population rule: no call below MIN_N per arm
    assert math.isfinite(S.z_final(1))
