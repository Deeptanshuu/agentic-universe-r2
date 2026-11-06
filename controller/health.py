"""Caller-ID health and rotation (C5).

Caller IDs (DIDs) are treated as a shared, fatigable resource, not as variants to pick a winner from.
Our own data (SUBMISSION.md §5, "Call time and caller ID") shows a DID's pickup sliding while it carries a
large share of a workflow's volume and recovering once rested — the familiar carrier spam-labelling effect.
Concentrating traffic on one "winning" DID burns it. So the policy is a rotation pool:

    candidate ──probe (small share)──▶ probing ──enough dials, not worse──▶ active
        ▲                                 │                                   │
        └──── cooldown 48h ◀── flagged ◀──┴── significantly worse than pool ──┘

* target allocation = PROBE_SHARE to each probing DID, the rest split EQUALLY across active DIDs;
* detection compares a DID with the other pool DIDs dialled in the same hours (stratified by hour), so
  time-of-day and lead mix cancel; with a single DID in use we fall back to its own same-hour baseline;
* we never flag the last usable DID (that would stop the workflow): we page a human instead;
* removing a flagged DID is urgent; probing/equalising is routine. Both are operational changes:
  auto-applied (stepping within max_shift) where the policy allows caller_id, otherwise sent for approval
  (routine ones only at the experiment looks, inside approver hours, within the request budget).
"""
from __future__ import annotations

import json
from collections import defaultdict

from . import settings as S
from .analysis import arm_data
from .stats import shrunk_rate, stratified_diff, tv_distance
from .wf import WF, clone, dist_of, set_dist, step_towards


class Health:
    def __init__(self, ctl):
        self.ctl = ctl
        self.store = ctl.store

    # ------------------------------------------------------------------ state
    def ensure(self, wf: WF, cfg: dict, hb: int) -> None:
        live = dist_of(cfg, "default", "caller_id")
        for d in wf.options.get("caller_id", []):
            self.store.x("INSERT OR IGNORE INTO dids(wf, did, state, since_hb, detail) VALUES(?,?,?,?,?)",
                         (wf.id, d, "active" if live.get(d, 0) > 0 else "candidate", hb, "{}"))

    def states(self, wf_id: str) -> dict[str, tuple[str, int]]:
        return {d: (s, h) for d, s, h in self.store.q("SELECT did, state, since_hb FROM dids WHERE wf=?", (wf_id,))}

    def excluded(self, wf_id: str) -> set[str]:
        return {d for d, (s, _) in self.states(wf_id).items() if s == "flagged"}

    def _set(self, wf_id: str, did: str, state: str, hb: int, detail: dict | None = None) -> None:
        self.store.x("UPDATE dids SET state=?, since_hb=?, detail=? WHERE wf=? AND did=?",
                     (state, hb, json.dumps(detail or {}), wf_id, did))

    # ------------------------------------------------------------------ detection
    def _window(self, wf: WF, hb: int, frm: int):
        rows = self.store.q("SELECT cid, hb, n, conn FROM cid_stats WHERE wf=? AND hb>=? AND hb<=?", (wf.id, frm, hb))
        by = defaultdict(dict)
        tot = defaultdict(int)
        for cid, h, n, c in rows:
            by[cid][h] = (n, c)
            tot[cid] += n
        return by, tot

    def _vs_pool(self, wf: WF, cid: str, others: list[str], frm: int):
        """DID vs the other pool DIDs, stratified by (day, config version, cohort). Within a stratum every
        DID was drawn from the same allocation for the same leads, so lead mix (incl. campaign age on
        next_campaign_only workflows, where only new campaigns see a new DID) cancels out."""
        return arm_data(self.store, wf.id, "caller_id", "all", frm).cmp_vs_pool(cid, others, "pickup_rate")

    def _vs_own_baseline(self, wf: WF, cid: str, hb: int):
        recent = self.store.q("SELECT hb % 24, SUM(n), SUM(conn) FROM cid_stats WHERE wf=? AND cid=? AND hb>=? "
                              "GROUP BY hb % 24", (wf.id, cid, hb - S.CID_WINDOW_H))
        base = dict((h, (n, c)) for h, n, c in self.store.q(
            "SELECT ((hb % 24) + 24) % 24, SUM(n), SUM(conn) FROM cid_stats WHERE wf=? AND cid=? AND hb>=? AND hb<? "
            "GROUP BY ((hb % 24) + 24) % 24", (wf.id, cid, hb - 24 * S.CID_BASELINE_DAYS, hb - S.CID_WINDOW_H)))
        return stratified_diff([(c, n, base.get(h, (0, 0))[1], base.get(h, (0, 0))[0]) for h, n, c in recent])

    def detect(self, wf: WF, hb: int) -> None:
        st = self.states(wf.id)
        usable = [d for d, (s, _) in st.items() if s in ("active", "probing")]
        by, tot = self._window(wf, hb, hb - S.CID_WINDOW_H)
        for did in sorted(usable):
            state, since = st[did]
            actives = [o for o in usable if o != did and st[o][0] == "active"]
            if state == "probing":
                # a probe is judged against PROVEN DIDs only (never against another unknown)
                pby, ptot = self._window(wf, hb, since)
                ref = [o for o in actives if ptot.get(o, 0) > 0]
                if ptot.get(did, 0) < S.CID_MIN_N:
                    continue
                if not ref:  # nothing proven to compare with: promote on volume alone
                    if ptot[did] >= S.PROBE_MIN_N:
                        with self.store.tx():
                            self._set(wf.id, did, "active", hb, {"reason": "no proven DID to compare with"})
                            self.store.decide(wf.id, "did_active", f"caller ID {did} promoted (no proven DID left "
                                              "to compare against)", None)
                    continue
                cmp = self._vs_pool(wf, did, ref, since)
                rel = cmp.diff / cmp.p_b if cmp.p_b and cmp.p_b == cmp.p_b else 0.0
                det = {"pickup": round(cmp.p_a, 4), "reference": round(cmp.p_b, 4), "relative_drop": round(rel, 3),
                       "z": round(cmp.z, 2), "n": cmp.n_a, "basis": "probe vs proven DIDs " + ",".join(ref),
                       "window_h": hb - since}
                if cmp.z <= -S.Z_PROBE_FAIL and rel <= -S.PROBE_REL_DROP:
                    self._flag(wf, did, hb, det, probe=True)
                    usable = [u for u in usable if u != did]
                elif ptot[did] >= S.PROBE_MIN_N and cmp.z > -1.0:
                    with self.store.tx():
                        self._set(wf.id, did, "active", hb, det)
                        self.store.decide(wf.id, "did_active", f"probe of caller ID {did} passed: pickup "
                                          f"{det['pickup']:.3f} vs proven {det['reference']:.3f} (z={det['z']}, "
                                          f"n={det['n']}); adding it to the rotation", det)
                continue
            if tot.get(did, 0) < S.CID_MIN_N:
                continue
            others = [o for o in usable if o != did and tot.get(o, 0) > 0]
            if others:
                cmp = self._vs_pool(wf, did, others, hb - S.CID_WINDOW_H)
                basis = "concurrent pool DIDs " + ",".join(others)
            else:
                cmp = self._vs_own_baseline(wf, did, hb)
                basis = f"own pickup in the same hours over the previous {S.CID_BASELINE_DAYS} days"
            ref = cmp.p_b
            if not ref or ref != ref or cmp.n_b <= 0:
                continue
            rel = cmp.diff / ref
            if cmp.z <= -S.Z_CID and rel <= -S.CID_REL_DROP:
                det = {"pickup": round(cmp.p_a, 4), "reference": round(ref, 4), "relative_drop": round(rel, 3),
                       "z": round(cmp.z, 2), "n": cmp.n_a, "basis": basis, "window_h": S.CID_WINDOW_H}
                if len(usable) <= 1 and not [d for d, (s, _) in st.items() if s == "candidate"]:
                    with self.store.tx():
                        self.store.alert(wf.id, "page", "last_did_degraded", f"{wf.id}: last usable caller ID {did} "
                                         f"is degraded ({rel:+.0%}); no alternative DID — needs new numbers")
                    continue
                self._flag(wf, did, hb, det, probe=False)
                usable = [u for u in usable if u != did]

    def guard_dids(self, wf: WF, hb: int) -> None:
        """Guardrail on the OTHER KPI: a DID can buy pickup by reaching people who don't stay on the call.
        If a DID's engagement on concurrent traffic is significantly worse than the rest of the pool by more
        than the guardrail margin, it leaves the rotation (same flag path, reason recorded)."""
        st = self.states(wf.id)
        usable = [d for d, (s, _) in st.items() if s in ("active", "probing")]
        if len(usable) < 2:
            return
        gm, gmax = wf.guardrail
        margin = gmax if gm == "engagement_rate" else S.DID_ENG_MAX_DROP
        intro = dict(self.store.q("SELECT did, introduced_hb FROM dids WHERE wf=? AND introduced_hb IS NOT NULL",
                                  (wf.id,)))
        for did in sorted(d for d in usable if d in intro):  # only DIDs WE brought in are on trial
            data = arm_data(self.store, wf.id, "caller_id", "all", max(intro[did], hb - S.GUARD_WINDOW_H))  # noqa
            others = [o for o in usable if o != did]
            c = data.cmp_vs_pool(did, others, "engagement_rate")
            if c.n_a < S.GUARD_MIN_CONN or c.n_b < S.GUARD_MIN_CONN:
                continue
            if c.diff <= -margin and c.z <= -S.Z_GUARD:
                det = {"pickup": round(data.rate(did, "pickup_rate") or 0, 4), "reference": round(c.p_b, 4),
                       "relative_drop": round(c.diff / c.p_b, 3) if c.p_b else 0, "z": round(c.z, 2), "n": c.n_a,
                       "basis": f"ENGAGEMENT guardrail: {c.p_a:.3f} vs pool {c.p_b:.3f} on concurrent traffic "
                                f"(margin {margin})", "window_h": S.GUARD_WINDOW_H, "engagement_diff": round(c.diff, 4)}
                self._flag(wf, did, hb, det, probe=st[did][0] == "probing")
                return  # one at a time; re-evaluate with the smaller pool next look

    def guardrail_watch(self, wf: WF, hb: int) -> None:
        """Workflow-level guardrail vs the workflow's own history (pre-D0). Breach -> ticket + freeze
        routine caller-ID expansion (no new probes) until it clears."""
        gm, gmax = wf.guardrail
        s_col, t_col = ("eng", "conn") if gm == "engagement_rate" else ("conn", "n")
        now = self.store.q1(f"SELECT SUM({s_col}), SUM({t_col}) FROM kpi_stats WHERE wf=? AND hb>=?", (wf.id, hb - 72))
        base = self.store.q1(f"SELECT SUM({s_col}), SUM({t_col}) FROM kpi_stats WHERE wf=? AND hb<0 AND hb>=?",
                             (wf.id, -24 * 7))
        if not now[1] or not base[1] or now[1] < 300:
            return
        p1, p0 = now[0] / now[1], base[0] / base[1]
        se = (p1 * (1 - p1) / now[1] + p0 * (1 - p0) / base[1]) ** 0.5
        z = (p1 - p0) / se if se else 0
        frozen = self.store.get(f"guard_freeze:{wf.id}")
        breach = (p1 - p0) <= -gmax and z <= -2.0
        if breach and not frozen:
            with self.store.tx():
                self.store.put(f"guard_freeze:{wf.id}", hb)
                self.store.decide(wf.id, "guardrail_breach", f"{gm} last 72h {p1:.3f} vs history {p0:.3f} "
                                  f"(drop {p0 - p1:.3f} > {gmax}, z={z:.1f}); freezing caller-ID expansion",
                                  {"now": p1, "history": p0, "z": z})
                self.store.alert(wf.id, "ticket", "guardrail_breach", f"{wf.id}: {gm} {p1:.3f} vs {p0:.3f} history "
                                 f"(> {gmax} drop) — check recent changes in `controller status`")
        elif frozen and not breach and (p1 - p0) > -gmax / 2:
            with self.store.tx():
                self.store.put(f"guard_freeze:{wf.id}", None)
                self.store.decide(wf.id, "guardrail_clear", f"{gm} back within margin ({p1:.3f} vs {p0:.3f})", None)

    def _flag(self, wf: WF, did: str, hb: int, det: dict, probe: bool) -> None:
        prev = self.store.q1("SELECT COUNT(*) FROM incidents WHERE wf=? AND kind='caller_id' AND key=?", (wf.id, did))[0]
        det = dict(det, times_flagged=prev + 1)
        with self.store.tx():
            self._set(wf.id, did, "flagged", hb, det)
            self.store.x("INSERT INTO incidents(wf, kind, key, status, opened_sim, opened_hb, detail) "
                         "VALUES(?,?,?,?,?,?,?)", (wf.id, "caller_id", did, "open", self.store.sim_ts, hb, json.dumps(det)))
            self.store.decide(wf.id, "incident_open",
                              f"caller ID {did} {'failed its probe' if probe else 'degraded'}: pickup {det['pickup']:.3f} "
                              f"vs {det['reference']:.3f} ({det['relative_drop']:+.0%}, z={det['z']}) — {det['basis']}",
                              det)
            if not probe:
                self.store.alert(wf.id, "ticket", "caller_id_degraded",
                                 f"{wf.id}: DID {did} pickup {det['relative_drop']:+.0%} vs {det['basis']}; rotated "
                                 "out automatically — check the number's spam label with the carrier (next business day)")

    def cooldown(self, wf: WF, hb: int) -> None:
        """A flagged DID may be re-probed ONCE after resting (fatigued numbers recover; spam-labelled ones
        don't, and a second flag retires the number for the run)."""
        for d, (s, since) in self.states(wf.id).items():
            times = self.store.q1("SELECT COUNT(*) FROM incidents WHERE wf=? AND kind='caller_id' AND key=?",
                                  (wf.id, d))[0]
            if s == "flagged" and hb - since >= S.DID_COOLDOWN_H and times < 2:
                with self.store.tx():
                    self._set(wf.id, d, "candidate", hb, {"reason": "cooldown over; eligible for re-probe"})
                    self.store.decide(wf.id, "did_cooldown", f"caller ID {d} rested {S.DID_COOLDOWN_H}h; eligible "
                                      "for a new probe", None)

    # ------------------------------------------------------------------ target + action
    def target(self, wf: WF, start_probes: bool) -> dict:
        st = self.states(wf.id)
        active = sorted(d for d, (s, _) in st.items() if s == "active")
        probing = sorted(d for d, (s, _) in st.items() if s == "probing")
        cands = sorted(d for d, (s, _) in st.items() if s == "candidate")
        if start_probes:
            probing = sorted(probing + cands)
        if not active and not probing:
            return {}
        if not active:  # nothing proven left: spread evenly over the unknowns
            return {d: 1.0 / len(probing) for d in probing}
        share = S.PROBE_SHARE if wf.auto_allowed({"caller_id"}) else S.PROBE_SHARE_APPROVAL
        ps = min(share, 0.5 / max(1, len(probing))) if probing else 0.0
        rest = 1.0 - ps * len(probing)
        out = {d: rest / len(active) for d in active}
        out.update({d: ps for d in probing})
        return out

    def act(self, wf: WF, hb: int, look: bool) -> None:
        if len(wf.options.get("caller_id", [])) < 1 or self.ctl.gov.inflight(wf.id, "caller_health"):
            return
        cur = self.ctl.live_config(wf.id)
        cfg = cur["config"]
        live = dist_of(cfg, "default", "caller_id")
        bad = self.excluded(wf.id)
        urgent = any(live.get(d, 0) > 0 for d in bad)
        sup = self.ctl.supervisor_touched(wf.id, "caller_id", hb)
        auto = wf.auto_allowed({"caller_id"}) and not sup
        if not urgent and sup:
            return  # a supervisor set caller IDs recently: leave routine rotation alone for the pause window
        routine_ok = auto or (look and self.ctl.gov.can_request(wf, urgent=False))
        start_probes = routine_ok and not urgent and not self.store.get(f"guard_freeze:{wf.id}")
        st0 = self.states(wf.id)
        if urgent and not any(s == "active" for s, _ in st0.values()):
            start_probes = True  # the last proven DID is out: replacing it with candidates is part of the fix
        tgt = self.target(wf, start_probes)
        if not tgt:
            return
        if tv_distance(live, tgt) < S.MATERIAL_TV:
            self._resolve(wf)
            return
        if not urgent and not routine_ok:
            return
        new = clone(cfg)
        step = step_towards(live, tgt, wf.max_shift * 0.95) if auto else tgt
        set_dist(new, "default", "caller_id", step)
        # cohorts that pinned their own caller_id (supervisor-defined) keep it, minus flagged DIDs
        for c in cfg.get("cohorts", []):
            own = (cfg.get("allocations", {}).get(c["id"]) or {}).get("caller_id")
            if own and set(own) & bad:
                keep = {k: v for k, v in own.items() if k not in bad} or step
                set_dist(new, c["id"], "caller_id", keep)
        st = self.states(wf.id)
        new_probes = [d for d, w in step.items() if w > 0 and st.get(d, ("", 0))[0] == "candidate"]
        removed = sorted(d for d in bad if live.get(d, 0) > 0)
        parts = []
        if removed:
            parts.append(f"rotate out degraded caller ID {', '.join(removed)}")
        if new_probes:
            parts.append(f"probe caller ID {', '.join(new_probes)} at a small share")
        if not parts:
            parts.append("rebalance dials evenly across healthy caller IDs")
        summary = f"{wf.id}: " + "; ".join(parts)
        dry = self._dry_run(wf, live, step, hb)
        rationale = ("Caller IDs are rotated as a pool: dials spread evenly over healthy DIDs (no single DID carries "
                     "the workflow, and sustained concentration has been followed by pickup decline in our data), "
                     "unknown DIDs enter at a small probe share and are promoted only if their pickup holds up against "
                     "the pool, degraded DIDs are removed. Operational change only: greeting, voice and script are "
                     "unchanged.")
        aff = self.ctl.affected_leads(wf, tv_distance(live, step) if not removed else 1.0, hb)
        open_ids = [r[0] for r in self.store.q("SELECT id FROM incidents WHERE wf=? AND kind='caller_id' AND "
                                              "status='open'", (wf.id,))]
        pid = self.ctl.gov.submit(wf, kind="caller_health", new_cfg=new, plan={"incidents": open_ids, "probes": new_probes},
                                  urgent=urgent, summary=summary, affected=aff, dry_run=dry,
                                  impact=0.7 if urgent else max(0.05, min(1.0, abs(dry["expected_delta_pickup"]) / 0.03)),
                                  rationale=rationale, evidence={"incidents": open_ids, "did_states": st},
                                  prefer_auto=auto)
        if pid:
            self.sync(wf, hb)

    def sync(self, wf: WF, hb: int) -> None:
        """A candidate whose share actually went live is now probing (two-phase, like experiments)."""
        live = dist_of(self.ctl.live_config(wf.id)["config"], "default", "caller_id")
        for d, (s, _) in self.states(wf.id).items():
            if s == "candidate" and live.get(d, 0) > 0:
                with self.store.tx():
                    self._set(wf.id, d, "probing", hb, {"share": live[d]})
                    self.store.x("UPDATE dids SET introduced_hb=COALESCE(introduced_hb, ?) WHERE wf=? AND did=?",
                                 (hb, wf.id, d))
                    self.store.decide(wf.id, "did_probe", f"caller ID {d} now live at {live[d]:.0%} (probe)", None)

    def _dry_run(self, wf: WF, live: dict, new: dict, hb: int) -> dict:
        rows = self.store.q("SELECT cid, SUM(n), SUM(conn) FROM cid_stats WHERE wf=? AND hb>=? GROUP BY cid",
                            (wf.id, hb - 24))
        obs = {c: (n, k) for c, n, k in rows}
        tn = sum(n for n, _ in obs.values())
        mean = (sum(k for _, k in obs.values()) / tn) if tn else 0.25
        def exp(d):
            return sum(w * shrunk_rate(obs.get(c, (0, 0))[1], obs.get(c, (0, 0))[0], mean) for c, w in d.items())
        vol = {c: n for c, (n, _) in obs.items()}
        return {"method": "last 24h pickup per caller ID (shrunk to the workflow mean; unseen DIDs assumed at the mean)",
                "observed_24h": {c: {"dials": n, "pickup": round(k / n, 4) if n else None} for c, (n, k) in obs.items()},
                "expected_pickup_live": round(exp(live), 4), "expected_pickup_proposed": round(exp(new), 4),
                "expected_delta_pickup": round(exp(new) - exp(live), 4), "dial_volume_24h": vol,
                "from": live, "to": new}

    def _resolve(self, wf: WF) -> None:
        live = dist_of(self.ctl.live_config(wf.id)["config"], "default", "caller_id")
        rows = self.store.q("SELECT id, key FROM incidents WHERE wf=? AND kind='caller_id' AND status='open'", (wf.id,))
        done = [(i, k) for i, k in rows if live.get(k, 0) == 0]
        if not done:
            return
        with self.store.tx():
            for i, k in done:
                self.store.x("UPDATE incidents SET status='resolved', resolved_sim=? WHERE id=?", (self.store.sim_ts, i))
                self.store.decide(wf.id, "incident_resolved", f"caller-ID incident {i}: {k} has no traffic in the live "
                                  "config", None)

    def tick(self, hb: int, dialling: bool, look: bool) -> None:
        for wf in self.ctl.wfs.values():
            self.cooldown(wf, hb)
            self.sync(wf, hb)
            if dialling:
                self.detect(wf, hb)
            if look:
                self.guard_dids(wf, hb)
                self.guardrail_watch(wf, hb)
            self.act(wf, hb, look)
            self._resolve(wf)
