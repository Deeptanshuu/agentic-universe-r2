"""Caller-ID health (C5): detect a degraded DID from the population, rotate away from it, verify.

Detection compares a DID's pickup with the other DIDs dialled IN THE SAME HOURS (stratified by hour bucket),
so time-of-day and lead-mix effects cancel. If a workflow dials from a single DID there is no concurrent
control; we then compare it with its own pickup in the same hours-of-day over the previous week.

Rotation is an operational change: auto-applied where the policy permits (stepping within max_shift),
otherwise sent as an urgent approval. Unused DIDs are only brought in when no proven-healthy DID remains,
and then spread evenly so one bad unknown DID can't take the whole workflow down.
"""
from __future__ import annotations

import json
from collections import defaultdict

from . import settings as S
from .stats import stratified_diff
from .wf import WF, clone, dist_of, set_dist, step_towards


class Health:
    def __init__(self, ctl):
        self.ctl = ctl
        self.store = ctl.store

    def excluded(self, wf_id: str) -> set[str]:
        return {r[0] for r in self.store.q("SELECT key FROM incidents WHERE wf=? AND kind='caller_id'", (wf_id,))}

    # ------------------------------------------------------------------ detection
    def detect(self, wf: WF, hb: int) -> None:
        rows = self.store.q("SELECT cid, hb, n, conn FROM cid_stats WHERE wf=? AND hb>=? AND hb<=?",
                            (wf.id, hb - S.CID_WINDOW_H, hb))
        by = defaultdict(dict)
        tot = defaultdict(int)
        for cid, h, n, c in rows:
            by[cid][h] = (n, c)
            tot[cid] += n
        bad = self.excluded(wf.id)
        active = [c for c in sorted(by) if tot[c] >= S.CID_MIN_N and c not in bad]
        for cid in active:
            others = [o for o in by if o != cid and o not in bad]
            if others:
                strata = []
                for h, (n, c) in by[cid].items():
                    on = sum(by[o].get(h, (0, 0))[0] for o in others)
                    oc = sum(by[o].get(h, (0, 0))[1] for o in others)
                    strata.append((c, n, oc, on))
                cmp = stratified_diff(strata)
                ref = cmp.p_b
                basis = "concurrent DIDs " + ",".join(others)
            else:
                cmp, ref = self._vs_own_baseline(wf, cid, hb)
                basis = f"own pickup in the same hours over the previous {S.CID_BASELINE_DAYS} days"
            if ref and ref == ref and cmp.n_b > 0:
                rel = cmp.diff / ref
                if cmp.z <= -S.Z_CID and rel <= -S.CID_REL_DROP:
                    self._open(wf, cid, hb, {"pickup": round(cmp.p_a, 4), "reference": round(ref, 4),
                                             "relative_drop": round(rel, 3), "z": round(cmp.z, 2), "n": cmp.n_a,
                                             "basis": basis, "window_h": S.CID_WINDOW_H})

    def _vs_own_baseline(self, wf: WF, cid: str, hb: int):
        recent = self.store.q("SELECT hb % 24, SUM(n), SUM(conn) FROM cid_stats WHERE wf=? AND cid=? AND hb>=? "
                              "GROUP BY hb % 24", (wf.id, cid, hb - S.CID_WINDOW_H))
        base = dict((h, (n, c)) for h, n, c in self.store.q(
            "SELECT ((hb % 24) + 24) % 24, SUM(n), SUM(conn) FROM cid_stats WHERE wf=? AND cid=? AND hb>=? AND hb<? "
            "GROUP BY ((hb % 24) + 24) % 24", (wf.id, cid, hb - 24 * S.CID_BASELINE_DAYS, hb - S.CID_WINDOW_H)))
        strata = [(c, n, base.get(h, (0, 0))[1], base.get(h, (0, 0))[0]) for h, n, c in recent]
        cmp = stratified_diff(strata)
        return cmp, cmp.p_b

    def _open(self, wf: WF, cid: str, hb: int, detail: dict) -> None:
        with self.store.tx():
            self.store.x("INSERT INTO incidents(wf, kind, key, status, opened_sim, opened_hb, detail) "
                         "VALUES(?,?,?,?,?,?,?)", (wf.id, "caller_id", cid, "open", self.store.sim_ts, hb,
                                                   json.dumps(detail)))
            self.store.decide(wf.id, "incident_open",
                              f"caller ID {cid} degraded: pickup {detail['pickup']:.3f} vs {detail['reference']:.3f} "
                              f"({detail['relative_drop']:+.0%}, z={detail['z']}) — {detail['basis']}", detail)
            self.store.alert(wf.id, "ticket", "caller_id_degraded",
                             f"{wf.id}: DID {cid} pickup {detail['relative_drop']:+.0%} vs {detail['basis']}; "
                             "controller is rotating it out — check the number's spam/STIR status with the carrier")

    # ------------------------------------------------------------------ action
    def target(self, wf: WF, cfg: dict) -> dict:
        """Caller-ID distribution per cohort with every flagged DID removed."""
        bad = self.excluded(wf.id)
        out = {}
        cohorts = ["default"] + [c["id"] for c in cfg.get("cohorts", [])
                                 if "caller_id" in (cfg.get("allocations", {}).get(c["id"]) or {})]
        for coh in cohorts:
            live = dist_of(cfg, coh, "caller_id")
            if not (set(live) & bad):
                continue
            keep = {k: v for k, v in live.items() if k not in bad}
            if not keep:  # no proven DID left: spread over all unflagged options (explore safely)
                opts = [o for o in wf.options.get("caller_id", []) if o not in bad]
                keep = {o: 1.0 for o in opts}
            if wf.id in self.ctl.wfs and "caller_id" in wf.experiment_dims() and coh == "default":
                sc = self.store.q1("SELECT mode, champion, incumbent, excluded FROM scopes WHERE wf=? AND cohort=? "
                                   "AND dim='caller_id'", (wf.id, coh))
                if sc and sc[0] in ("explore", "exploit"):
                    d = self.ctl.exp.dist_for(wf, "caller_id", sc[0], sc[1], sc[2], json.loads(sc[3] or "[]"))
                    if d:
                        keep = d
            s = sum(keep.values())
            out[coh] = {k: v / s for k, v in keep.items()}
        return out

    def act(self, wf: WF, hb: int) -> None:
        if not self.excluded(wf.id) or self.ctl.gov.inflight(wf.id, "caller_health"):
            return
        cur = self.ctl.live_config(wf.id)
        cfg = cur["config"]
        tgt = self.target(wf, cfg)
        open_ids = [r[0] for r in self.store.q("SELECT id FROM incidents WHERE wf=? AND kind='caller_id' AND "
                                              "status='open'", (wf.id,))]
        if not tgt:
            self._resolve(wf, open_ids)
            return
        sup_recent = self.ctl.supervisor_touched(wf.id, "caller_id", hb)
        auto = wf.auto_allowed({"caller_id"}) and not sup_recent
        new = clone(cfg)
        for coh, d in tgt.items():
            live = dist_of(cfg, coh, "caller_id")
            set_dist(new, coh, "caller_id", step_towards(live, d, wf.max_shift * 0.95) if auto else d)
        bad = sorted(self.excluded(wf.id))
        summary = f"{wf.id}: rotate caller ID away from degraded {', '.join(bad)}"
        aff = self.ctl.affected_leads(wf, 1.0, hb)
        dry = {"method": "caller-ID pickup over the detection window, stratified by hour",
               "incidents": [json.loads(r[0]) for r in self.store.q(
                   "SELECT detail FROM incidents WHERE wf=? AND kind='caller_id' AND status='open'", (wf.id,))],
               "proposed_caller_id": {c: d for c, d in tgt.items()}}
        self.ctl.gov.submit(wf, kind="caller_health", new_cfg=new, plan={"incidents": open_ids}, urgent=True,
                            summary=summary, affected=aff, dry_run=dry, impact=0.7,
                            rationale=f"Population-level pickup drop on {', '.join(bad)} (see dry_run). Operational "
                                      "change: moves dials to healthy caller IDs; message, voice and script unchanged.",
                            evidence={"incidents": open_ids}, prefer_auto=auto)

    def _resolve(self, wf: WF, ids: list[int]) -> None:
        if not ids:
            return
        with self.store.tx():
            for i in ids:
                self.store.x("UPDATE incidents SET status='resolved', resolved_sim=? WHERE id=?", (self.store.sim_ts, i))
                self.store.decide(wf.id, "incident_resolved", f"caller-ID incident {i}: flagged DID has no traffic in "
                                  "the live config", None)

    def tick(self, hb: int, dialling: bool) -> None:
        for wf in self.ctl.wfs.values():
            if dialling:
                self.detect(wf, hb)
            self.act(wf, hb)
