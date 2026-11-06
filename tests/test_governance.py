"""Governance client: write-ahead, crash reconciliation, stale-base handling, budgets.

A fake simulator stands in for the HTTP API so we can inject failures at the exact points that matter:
the request reached the simulator but the response was lost (crash/timeout), apply succeeded server-side
but the controller died before recording it, a supervisor edit made the base stale, etc.
Run: python -m pytest -q tests/test_governance.py
"""
import copy

import pytest

from controller.client import ApiError
from controller.governance import Governance
from controller.store import Store
from controller.wf import WF

SPEC = {"workflow_id": "w", "tenant_id": "t",
        "options": {"welcome_message": ["W1", "W2"], "voice": ["V1", "V2"], "caller_id": ["D1", "D2"],
                    "prompt_version": ["P1"]},
        "objective": {"primary": "engagement_rate", "guardrail": {"pickup_rate_max_drop": 0.02}},
        "governance": {"auto_apply": {"enabled": True, "allowed_dims": ["caller_id"], "max_shift": 0.5},
                       "approver_hours": [10, 19], "max_requests_per_24h": 3, "proposal_ttl_hours": 24,
                       "apply_window_hours": 2},
        "dialling_window": [9, 21], "mid_flight": "allowed", "cadence": [2, 2], "daily_batch": {"size": 100}}

BASE_CFG = {"cohorts": [], "allocations": {"default": {"welcome_message": {"W1": 1.0}, "voice": {"V1": 1.0},
                                                       "caller_id": {"D1": 0.5, "D2": 0.5}}},
            "prompt_version": "P1", "schedule": {"hour_weights": {}, "lead_preferred_hour": {}}}


class FakeSim:
    def __init__(self):
        self.version = 1
        self.config = copy.deepcopy(BASE_CFG)
        self.history = [{"version": 1, "proposal_id": None, "approval_id": None}]
        self.apprs = {}
        self.posts = 0
        self.applies = 0
        self.lose_next_response = None  # "post" | "apply" | "auto"

    # --- endpoints used by Governance
    def config_(self, wf):
        return {"version": self.version, "config": copy.deepcopy(self.config)}

    def request_approval(self, body):
        if any(a["proposal_id"] == body["proposal_id"] for a in self.apprs.values()):
            raise ApiError(409, "duplicate_proposal_id", body["proposal_id"])
        if body["base_version"] != self.version:
            raise ApiError(409, "stale_base_version", {"current_version": self.version})
        self.posts += 1
        aid = f"apr_{len(self.apprs) + 1:05d}"
        self.apprs[aid] = {"approval_id": aid, "proposal_id": body["proposal_id"], "workflow_id": body["workflow_id"],
                           "status": "pending", "base_version": body["base_version"], "body": body}
        if self.lose_next_response == "post":
            self.lose_next_response = None
            raise ConnectionError("response lost")
        return {k: v for k, v in self.apprs[aid].items() if k != "body"}

    def approval(self, aid):
        return {k: v for k, v in self.apprs[aid].items() if k != "body"}

    def approvals(self, wf=None, status=None):
        return [self.approval(a) for a in self.apprs]

    def decide(self, aid, status):
        self.apprs[aid]["status"] = status

    def apply(self, wf, aid):
        a = self.apprs[aid]
        if a["status"] == "applied":
            raise ApiError(409, "already_applied", aid)
        if a["base_version"] != self.version:
            raise ApiError(409, "stale_base_version", {"current_version": self.version})
        self.applies += 1
        self._set(a["body"]["proposed_config"], a["proposal_id"], aid)
        a["status"] = "applied"
        if self.lose_next_response == "apply":
            self.lose_next_response = None
            raise ConnectionError("response lost")
        return {"workflow_id": wf, "version": self.version}

    def auto_apply(self, wf, body):
        if any(h["proposal_id"] == body["proposal_id"] for h in self.history):
            raise ApiError(409, "duplicate_proposal_id", body["proposal_id"])
        self._set(body["proposed_config"], body["proposal_id"], None)
        return {"workflow_id": wf, "version": self.version}

    def config_history(self, wf):
        return list(self.history)

    def supervisor_edit(self, mutate):
        cfg = copy.deepcopy(self.config)
        mutate(cfg)
        self._set(cfg, None, None)

    def _set(self, cfg, pid, aid):
        self.version += 1
        self.config = copy.deepcopy(cfg)
        self.history.append({"version": self.version, "proposal_id": pid, "approval_id": aid})


class FakeClient:
    def __init__(self, sim):
        self.sim = sim

    def __getattr__(self, name):
        return getattr(self.sim, name)

    def config(self, wf):
        return self.sim.config_(wf)


class Ctl:
    def __init__(self, tmp_path, sim):
        self.store = Store(str(tmp_path / "gov.sqlite"))
        self.client = FakeClient(sim)
        self.wfs = {"w": WF("w", SPEC)}
        self.hb, self.hour = 11, 11
        self.store.sim_ts, self.store.hb = "2027-01-04T11:00:00+05:30", 11
        self.applied, self.failed = [], []

    def live_config(self, wf, fresh=False):
        return self.client.config(wf)

    def invalidate(self, wf=None):
        pass

    def on_proposal_applied(self, p, version):
        self.applied.append((p["proposal_id"], version))

    def on_proposal_failed(self, p, status):
        self.failed.append((p["proposal_id"], status))


def _voice_change(ctl):
    cfg = copy.deepcopy(ctl.live_config("w")["config"])
    cfg["allocations"]["default"]["voice"] = {"V1": 0.5, "V2": 0.5}
    return cfg


def _submit(gov, ctl, cfg, **kw):
    args = dict(kind="experiment", new_cfg=cfg, plan={}, urgent=False, summary="s", affected=10,
                dry_run={"x": 1}, impact=0.2, rationale="r")
    args.update(kw)
    return gov.submit(ctl.wfs["w"], **args)


@pytest.fixture()
def env(tmp_path):
    sim = FakeSim()
    ctl = Ctl(tmp_path, sim)
    return sim, ctl, Governance(ctl)


def test_lost_post_response_is_reconciled_without_a_second_request(env):
    sim, ctl, gov = env
    sim.lose_next_response = "post"
    pid = _submit(gov, ctl, _voice_change(ctl))
    assert ctl.store.q1("SELECT status FROM proposals WHERE proposal_id=?", (pid,))[0] == "intent"
    gov2 = Governance(ctl)  # "restart"
    gov2.reconcile()
    st, aid = ctl.store.q1("SELECT status, approval_id FROM proposals WHERE proposal_id=?", (pid,))
    assert st == "pending" and aid == "apr_00001"
    assert sim.posts == 1  # the approval request was sent exactly once


def test_lost_apply_response_is_reconciled_without_double_apply(env):
    sim, ctl, gov = env
    pid = _submit(gov, ctl, _voice_change(ctl))
    sim.decide("apr_00001", "approved")
    sim.lose_next_response = "apply"
    gov.tick()
    assert ctl.store.q1("SELECT status FROM proposals WHERE proposal_id=?", (pid,))[0] == "applying"
    Governance(ctl).reconcile()  # restart
    st, ver = ctl.store.q1("SELECT status, applied_version FROM proposals WHERE proposal_id=?", (pid,))
    assert st == "applied" and ver == 2 and sim.applies == 1
    assert ctl.applied == [(pid, 2)]  # learning hooks ran once


def test_supervisor_edit_makes_apply_stale_and_we_never_force_it(env):
    sim, ctl, gov = env
    pid = _submit(gov, ctl, _voice_change(ctl))
    sim.supervisor_edit(lambda c: c.update(prompt_version="P1-sup"))
    sim.decide("apr_00001", "approved")
    gov.tick()
    assert ctl.store.q1("SELECT status FROM proposals WHERE proposal_id=?", (pid,))[0] == "stale"
    assert sim.config["prompt_version"] == "P1-sup"      # supervisor's edit survives
    assert ctl.failed == [(pid, "stale")]               # owning module told to re-plan on the new base
    # a re-proposal is built on the CURRENT config, so it carries the supervisor's edit forward
    pid2 = _submit(gov, ctl, _voice_change(ctl))
    body = ctl.store.q1("SELECT body FROM proposals WHERE proposal_id=?", (pid2,))[0]
    assert '"P1-sup"' in body and pid2 != pid


def test_expired_and_rejected_run_failure_hooks_once(env):
    sim, ctl, gov = env
    pid = _submit(gov, ctl, _voice_change(ctl))
    sim.decide("apr_00001", "expired")
    gov.tick()
    gov.tick()
    assert ctl.failed == [(pid, "expired")]


def test_request_budget_keeps_a_slot_for_incidents_and_respects_hours(env):
    sim, ctl, gov = env
    wf = ctl.wfs["w"]
    assert gov.can_request(wf, urgent=False)
    for _ in range(2):
        cfg = _voice_change(ctl)
        cfg["allocations"]["default"]["voice"] = {"V1": 0.5 + 0.1 * _, "V2": 0.5 - 0.1 * _}
        _submit(gov, ctl, cfg)
        sim.version += 0  # keep base valid
    assert not gov.can_request(wf, urgent=False)  # cap 3, one reserved
    assert gov.can_request(wf, urgent=True)


def test_routine_requests_only_inside_approver_hours(env):
    sim, ctl, gov = env
    wf = ctl.wfs["w"]
    ctl.hour = 21
    assert not gov.can_request(wf, urgent=False)
    ctl.hour = 18  # last approver hour is kept free of routine requests
    assert not gov.can_request(wf, urgent=False)
    ctl.hour = 10
    assert gov.can_request(wf, urgent=False)


def test_auto_apply_lost_response_reconciled_from_config_history(env):
    sim, ctl, gov = env
    cfg = copy.deepcopy(ctl.live_config("w")["config"])
    cfg["allocations"]["default"]["caller_id"] = {"D1": 0.8, "D2": 0.2}
    orig = sim.auto_apply

    def lossy(wf, body):
        orig(wf, body)
        raise ConnectionError("lost")
    sim.auto_apply = lossy
    pid = _submit(gov, ctl, cfg, kind="caller_health", urgent=True, prefer_auto=True)
    assert ctl.store.q1("SELECT status FROM proposals WHERE proposal_id=?", (pid,))[0] == "intent"
    sim.auto_apply = orig
    Governance(ctl).reconcile()
    assert ctl.store.q1("SELECT status, applied_version FROM proposals WHERE proposal_id=?", (pid,)) == ("applied", 2)
    assert sim.version == 2  # applied once
