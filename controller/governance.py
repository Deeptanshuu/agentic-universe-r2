"""Governance client (C9): the ONLY code path that changes a workflow's config.

Proposal state machine (status column of `proposals`):

    intent ──POST ok──▶ pending ──approved──▶ approved ──▶ applying ──200──▶ applied
      │                    ├──rejected──▶ rejected                └──409 stale──▶ stale   (re-plan, re-propose)
      │                    └──expired───▶ expired  (= rejection;   └──410────────▶ lapsed  (re-plan)
      │                                  re-queued with fresh data)
      ├──409 duplicate──▶ (reconcile by proposal_id via GET /approvals)
      ├──409 stale at submit──▶ stale_on_submit
      └──4xx────────────▶ invalid (alert)
    auto channel: intent ──PUT 200──▶ applied | 403 ──▶ auto_denied (falls back to approval if urgent)

Write-ahead: the `intent` row (with the full request body and a deterministic proposal_id) is committed
BEFORE the HTTP call. After a crash, `reconcile()` finds the outcome by proposal_id/approval_id in the
simulator (GET /approvals, GET /config/history) and never sends a second, different request. The
simulator rejects a reused proposal_id, so a blind resend can't double-submit either.
"""
from __future__ import annotations

import json

from . import settings as S
from .client import ApiError
from .wf import WF, changed_dims, diff_summary, max_shift_of

LIVE = ("intent", "pending", "approved", "applying")


class Governance:
    def __init__(self, ctl):
        self.ctl = ctl
        self.store = ctl.store

    # ------------------------------------------------------------------ queries
    def inflight(self, wf_id: str, kind: str | None = None) -> list:
        sql = f"SELECT proposal_id, kind, status FROM proposals WHERE wf=? AND status IN ({','.join('?' * len(LIVE))})"
        args = [wf_id, *LIVE]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        return self.store.q(sql, args)

    def requests_in(self, wf_id: str, hb_from: int) -> int:
        return self.store.q1("SELECT COUNT(*) FROM proposals WHERE wf=? AND channel='approval' AND sent_hb IS NOT NULL"
                             " AND sent_hb >= ? AND approval_id IS NOT NULL", (wf_id, hb_from))[0]

    def can_request(self, wf: WF, urgent: bool) -> bool:
        hb, hour = self.ctl.hb, self.ctl.hour
        cap = int(wf.gov.get("max_requests_per_24h", 1))
        used = self.requests_in(wf.id, hb - 23)  # 24 hourly buckets incl. current
        if urgent:
            return used < cap
        reserve = S.REQUEST_RESERVE if cap >= 2 else 0
        if used >= cap - reserve:
            return False
        a, b = wf.approver_hours
        if not (a <= hour < b - S.APPROVAL_SUBMIT_LAST_HOUR_MARGIN):
            return False
        pref = S.supervisor_pref(wf.spec)
        if pref:
            if hour < pref.get("not_before_hour", 0):
                return False
            day_start = hb - hour
            if self.requests_in(wf.id, day_start) >= pref.get("max_requests_per_day", cap):
                return False
        return True

    # ------------------------------------------------------------------ submit
    def submit(self, wf: WF, kind: str, new_cfg: dict, plan: dict, urgent: bool, summary: str, affected: int,
               dry_run: dict, impact: float, rationale: str, evidence: dict | None = None,
               prefer_auto: bool = False) -> str | None:
        cur = self.ctl.live_config(wf.id, fresh=True)
        dims = changed_dims(cur["config"], new_cfg)
        if not dims:
            return None
        auto_ok = prefer_auto and wf.auto_allowed(dims) and max_shift_of(cur["config"], new_cfg) <= wf.max_shift + 1e-9
        if not auto_ok and not self.can_request(wf, urgent):
            self.store.decide(wf.id, "deferred", f"wanted ({kind}) but approval budget/hours don't allow: {summary}")
            return None
        channel = "auto" if auto_ok else "approval"
        with self.store.tx():
            seq = (self.store.get("proposal_seq", 0) or 0) + 1
            self.store.put("proposal_seq", seq)
            pid = f"{wf.id}.{kind}.{seq:05d}"
            ev = dict(evidence or {})
            diff = diff_summary(cur["config"], new_cfg)
            if channel == "approval":
                body = {"proposal_id": pid, "workflow_id": wf.id, "base_version": cur["version"],
                        "proposed_config": new_cfg, "change_summary": summary[:300], "diff": diff,
                        "affected_leads": int(max(0, affected)), "dry_run": dry_run,
                        "impact_score": float(min(1.0, max(0.0, impact))), "rationale": rationale[:4000],
                        "evidence": ev}
            else:
                body = {"proposal_id": pid, "base_version": cur["version"], "proposed_config": new_cfg,
                        "rationale": rationale[:4000], "evidence": ev}
            self.store.x("INSERT INTO proposals(proposal_id, wf, channel, kind, status, base_version, body, plan, summary,"
                         " changed_dims, created_sim) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (pid, wf.id, channel, kind, "intent", cur["version"], json.dumps(body), json.dumps(plan),
                          summary, json.dumps(sorted(dims)), self.store.sim_ts))
            self.store.decide(wf.id, "proposal", f"[{channel}] {summary}",
                              {"diff": diff, "impact": impact, "affected_leads": affected, "dry_run": dry_run,
                               "rationale": rationale, "evidence": ev}, proposal_id=pid, version=cur["version"])
        self._send(pid)
        return pid

    def _row(self, pid: str) -> dict:
        r = self.store.q1("SELECT proposal_id, wf, channel, kind, status, approval_id, base_version, body, plan, "
                          "changed_dims FROM proposals WHERE proposal_id=?", (pid,))
        keys = ("proposal_id", "wf", "channel", "kind", "status", "approval_id", "base_version", "body", "plan",
                "changed_dims")
        d = dict(zip(keys, r))
        d["body"] = json.loads(d["body"])
        d["plan"] = json.loads(d["plan"] or "{}")
        return d

    def _send(self, pid: str) -> None:
        p = self._row(pid)
        wf = p["wf"]
        try:
            if p["channel"] == "approval":
                resp = self.ctl.client.request_approval(p["body"])
                self._mark_pending(pid, resp)
            else:
                resp = self.ctl.client.auto_apply(wf, p["body"])
                self._mark_applied(pid, resp["version"], via="auto")
        except ApiError as e:
            if e.code == "duplicate_proposal_id":
                self._reconcile_one(pid)
            elif e.code == "stale_base_version":
                self._fail(pid, "stale_on_submit", [str(e.detail)])
            elif e.status == 403 and p["channel"] == "auto":
                self._fail(pid, "auto_denied", e.detail if isinstance(e.detail, list) else [str(e.detail)])
            else:
                self._fail(pid, "invalid", [f"{e.status} {e.code}: {e.detail}"])
                self.store.alert(wf, "ticket", "proposal_invalid", f"{pid}: {e.status} {e.code}")
        except ConnectionError:
            pass  # stays `intent`; reconciled next tick

    def _mark_pending(self, pid: str, resp: dict) -> None:
        with self.store.tx():
            self.store.x("UPDATE proposals SET status='pending', approval_id=?, sent_sim=?, sent_hb=?, expires_at=? "
                         "WHERE proposal_id=? AND status='intent'",
                         (resp["approval_id"], self.store.sim_ts, self.ctl.hb, resp.get("expires_at"), pid))
            self.store.decide(self._row(pid)["wf"], "request_sent", f"approval {resp['approval_id']} pending",
                              {"expires_at": resp.get("expires_at")}, proposal_id=pid, approval_id=resp["approval_id"])

    def _mark_applied(self, pid: str, version: int, via: str) -> None:
        p = self._row(pid)
        with self.store.tx():
            self.store.x("UPDATE proposals SET status='applied', applied_version=?, applied_sim=?, "
                         "sent_sim=COALESCE(sent_sim, ?), sent_hb=COALESCE(sent_hb, ?) WHERE proposal_id=?",
                         (version, self.store.sim_ts, self.store.sim_ts, self.ctl.hb, pid))
            self.store.x("INSERT INTO config_versions(wf, version, actor, changed_dims, proposal_id, approval_id,"
                         " applied_at, hb, config) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(wf, version) DO UPDATE SET"
                         " config=excluded.config, proposal_id=excluded.proposal_id",
                         (p["wf"], version, "controller_auto" if via == "auto" else "controller_approved",
                          p["changed_dims"], pid, p["approval_id"], self.store.sim_ts,
                          self.ctl.hb, json.dumps(p["body"]["proposed_config"])))
            self.store.decide(p["wf"], "applied", f"v{version} applied via {via}", None, proposal_id=pid,
                              approval_id=p["approval_id"], version=version)
            self.ctl.on_proposal_applied(p, version)
        self.ctl.invalidate(p["wf"])

    def _fail(self, pid: str, status: str, reasons: list) -> None:
        p = self._row(pid)
        with self.store.tx():
            self.store.x("UPDATE proposals SET status=?, reasons=?, decided_sim=COALESCE(decided_sim, ?) "
                         "WHERE proposal_id=?", (status, json.dumps(reasons), self.store.sim_ts, pid))
            self.store.decide(p["wf"], status, f"{pid} {status}: {'; '.join(map(str, reasons))[:300]}", None,
                              proposal_id=pid, approval_id=p["approval_id"])
            self.ctl.on_proposal_failed(p, status)

    # ------------------------------------------------------------------ control events (inside ingest tx)
    def on_decided_event(self, ev: dict) -> None:
        st = ev.get("status")
        new = {"approved": "approved", "rejected": "rejected", "expired": "expired"}.get(st)
        if not new:
            return
        self.store.x("UPDATE proposals SET status=?, reasons=?, decided_sim=? WHERE approval_id=? AND status='pending'",
                     (new, json.dumps(ev.get("reasons") or []), ev.get("ts"), ev.get("approval_id")))

    # ------------------------------------------------------------------ the tick
    def tick(self) -> None:
        rows = self.store.q(f"SELECT proposal_id, status FROM proposals WHERE status IN ({','.join('?' * len(LIVE))}) "
                            "ORDER BY proposal_id", LIVE)
        for pid, status in rows:
            if status == "intent":
                self._reconcile_one(pid)
            elif status == "pending":
                self._poll(pid)
            elif status in ("approved", "applying"):
                self._apply(pid)
        # terminal-by-event rows (rejected/expired set by events) still need their failure hooks run once
        for pid, status in self.store.q("SELECT proposal_id, status FROM proposals WHERE status IN "
                                        "('rejected','expired') AND (superseded_by IS NULL OR superseded_by='')"):
            self._finalise_failed(pid, status)

    def _finalise_failed(self, pid: str, status: str) -> None:
        p = self._row(pid)
        with self.store.tx():
            self.store.x("UPDATE proposals SET superseded_by='handled' WHERE proposal_id=?", (pid,))
            reasons = self.store.q1("SELECT reasons FROM proposals WHERE proposal_id=?", (pid,))[0]
            self.store.decide(p["wf"], status, f"{pid} {status}: {reasons}", None, proposal_id=pid,
                              approval_id=p["approval_id"])
            self.ctl.on_proposal_failed(p, status)

    def _poll(self, pid: str) -> None:
        p = self._row(pid)
        try:
            a = self.ctl.client.approval(p["approval_id"])
        except (ApiError, ConnectionError):
            return
        st = a.get("status")
        if st == "approved":
            with self.store.tx():
                self.store.x("UPDATE proposals SET status='approved', decided_sim=?, apply_by=? WHERE proposal_id=? "
                             "AND status='pending'", (a.get("decided_at"), a.get("apply_by"), pid))
            self._apply(pid)
        elif st in ("rejected", "expired"):
            with self.store.tx():
                self.store.x("UPDATE proposals SET status=?, reasons=?, decided_sim=?, superseded_by='handled' "
                             "WHERE proposal_id=?", (st, json.dumps(a.get("reasons") or []), a.get("decided_at"), pid))
                self.store.decide(p["wf"], st, f"{pid} {st}: {a.get('reasons')}", None, proposal_id=pid,
                                  approval_id=p["approval_id"])
                self.ctl.on_proposal_failed(p, st)
        elif st == "applied":
            self._reconcile_one(pid)
        elif st == "lapsed":
            self._fail(pid, "lapsed", ["apply window passed"])

    def _apply(self, pid: str) -> None:
        p = self._row(pid)
        with self.store.tx():
            self.store.x("UPDATE proposals SET status='applying' WHERE proposal_id=?", (pid,))
        try:
            r = self.ctl.client.apply(p["wf"], p["approval_id"])
            self._mark_applied(pid, r["version"], via="approval")
        except ApiError as e:
            if e.code == "stale_base_version":
                # A supervisor (or our own auto-apply) changed the config after we proposed. Never force it:
                # mark stale; the owning module re-plans on top of the NEW config and re-proposes.
                self._fail(pid, "stale", [f"config moved to v{(e.detail or {}).get('current_version')}"])
            elif e.status == 410:
                self._fail(pid, "lapsed", [str(e.detail)])
            elif e.status == 404:
                self._fail(pid, "invalid", [str(e.detail)])
            else:
                hist = self._history_hit(p)
                if hist:
                    self._mark_applied(pid, hist, via="approval")
                else:
                    self._fail(pid, "invalid", [f"{e.status} {e.code}: {e.detail}"])
        except ConnectionError:
            pass  # stays applying; reconciled next tick

    def _history_hit(self, p: dict) -> int | None:
        try:
            hist = self.ctl.client.config_history(p["wf"])
        except (ApiError, ConnectionError):
            return None
        for h in hist:
            if h.get("proposal_id") == p["proposal_id"] or (p["approval_id"] and h.get("approval_id") == p["approval_id"]):
                return h["version"]
        return None

    def _reconcile_one(self, pid: str) -> None:
        """Find out what really happened to a proposal whose outcome we didn't record (crash/timeout)."""
        p = self._row(pid)
        v = self._history_hit(p)
        if v is not None:
            self._mark_applied(pid, v, via=p["channel"])
            return
        if p["channel"] == "approval":
            try:
                found = [a for a in self.ctl.client.approvals(p["wf"]) if a.get("proposal_id") == pid]
            except (ApiError, ConnectionError):
                return
            if found:
                a = found[0]
                if p["status"] == "intent":
                    self._mark_pending(pid, a)
                with self.store.tx():
                    self.store.x("UPDATE proposals SET approval_id=? WHERE proposal_id=?", (a["approval_id"], pid))
                if a["status"] != "pending":
                    self._poll(pid)
                return
        if p["status"] == "intent":
            self._send(pid)  # never reached the simulator: safe to send the SAME body (same proposal_id)
        elif p["status"] == "applying":
            self._apply(pid)

    def reconcile(self) -> None:
        self.tick()
