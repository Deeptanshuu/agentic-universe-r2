"""Variant experimentation, promotion, decay detection and rollback (C6, C7) + shared cohorts (C2).

One state machine per scope = (workflow, cohort, dimension):

    idle ──look──▶ explore ──winner / max-n / timeout──▶ exploit ──decay──▶ explore (rollback)
                      │  └─ arm significantly worse ─▶ explore (arm excluded)      │
                      └──────── supervisor edits this dim ──▶ paused (24h) ◀───────┘

State transitions are TWO-PHASE: a look computes the wanted transition and ships it inside a proposal;
the scope only changes mode when that proposal is actually applied (Product: an arm that never ran
because permission wasn't given must not be learned from). Statistics only ever use per-call variants
that really happened, so a rejected/expired proposal cannot leak into learning.
"""
from __future__ import annotations

import json
import re

from . import settings as S
from .analysis import arm_data, segment_share, segments
from .ingest import FEATURES
from .stats import shrunk_rate, tv_distance
from .wf import WF, clone, dist_of, majority, set_dist


class Scope:
    __slots__ = ("wf", "cohort", "dim", "mode", "champion", "incumbent", "excluded", "mode_since_hb",
                 "explore_from_hb", "history", "inflight")

    def __init__(self, row):
        (self.wf, self.cohort, self.dim, self.mode, self.champion, self.incumbent, excl, self.mode_since_hb,
         self.explore_from_hb, hist, self.inflight) = row
        self.excluded = json.loads(excl or "[]")
        self.history = json.loads(hist or "[]")

    @property
    def key(self) -> str:
        return f"{self.cohort}/{self.dim}"


def slug(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", v.lower().replace("+", "plus"))


class Experiments:
    def __init__(self, ctl):
        self.ctl = ctl
        self.store = ctl.store

    # ------------------------------------------------------------------ scope bookkeeping
    def ensure_scopes(self, wf: WF, cfg: dict, hb: int) -> None:
        for dim in wf.experiment_dims():
            if not self.store.q1("SELECT 1 FROM scopes WHERE wf=? AND cohort='default' AND dim=?", (wf.id, dim)):
                m = majority(dist_of(cfg, "default", dim))
                self.store.x("INSERT INTO scopes(wf, cohort, dim, mode, champion, incumbent, excluded, mode_since_hb,"
                             " explore_from_hb, history, inflight) VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
                             (wf.id, "default", dim, "idle", m, m, "[]", hb, None, "[]"))

    def scopes(self, wf_id: str) -> list[Scope]:
        return [Scope(r) for r in self.store.q(
            "SELECT wf, cohort, dim, mode, champion, incumbent, excluded, mode_since_hb, explore_from_hb, history,"
            " inflight FROM scopes WHERE wf=? ORDER BY cohort='default' DESC, cohort, dim", (wf_id,))]

    def analysis_cohorts(self, wf_id: str, cohort: str, dim: str, cfg: dict) -> list[str]:
        """Which config cohorts' calls were allocated by this scope's distribution."""
        if cohort != "default":
            return [cohort]
        out = ["default"]
        for c in cfg.get("cohorts", []):
            if dim not in (cfg.get("allocations", {}).get(c["id"]) or {}):
                out.append(c["id"])
        return out

    # ------------------------------------------------------------------ desired allocation per mode
    def allowed_arms(self, wf: WF, dim: str, excluded: list[str]) -> list[str]:
        bad = set(excluded) | (self.ctl.health.excluded(wf.id) if dim == "caller_id" else set())
        return [o for o in wf.options.get(dim, []) if o not in bad]

    def dist_for(self, wf: WF, dim: str, mode: str, champion: str | None, incumbent: str | None,
                 excluded: list[str]) -> dict | None:
        opts = self.allowed_arms(wf, dim, excluded)
        if mode in ("idle", "paused") or not opts:
            return None
        if mode == "explore":
            inc = incumbent if incumbent in opts else opts[0]
            if len(opts) == 1:
                return {inc: 1.0}
            share = max(S.EXPLORE_INCUMBENT_SHARE, 1.0 / len(opts))
            rest = (1.0 - share) / (len(opts) - 1)
            return {o: (share if o == inc else rest) for o in opts}
        champ = champion if champion in opts else (incumbent if incumbent in opts else opts[0])
        others = [o for o in opts if o != champ]
        if not others:
            return {champ: 1.0}
        d = {champ: 1.0 - S.EXPLOIT_HOLDBACK}
        d.update({o: S.EXPLOIT_HOLDBACK / len(others) for o in others})
        return d

    # ------------------------------------------------------------------ evaluation
    def _guard_ok(self, wf: WF, data, a: str, b: str) -> tuple[bool, dict]:
        gm, gmax = wf.guardrail
        c = data.cmp(a, b, gm)
        ok = not (c.diff < -gmax or (c.z <= -2.33 and c.diff < -gmax / 2))
        return ok, {"guardrail": gm, "max_drop": gmax, **c.as_dict()}

    def evaluate(self, wf: WF, sc: Scope, cfg: dict, hb: int) -> list[dict]:
        metric = wf.primary
        cohorts = self.analysis_cohorts(wf.id, sc.cohort, sc.dim, cfg)
        base = {"wf": wf.id, "cohort": sc.cohort, "dim": sc.dim, "champion": sc.champion,
                "incumbent": sc.incumbent, "excluded": list(sc.excluded)}
        live = dist_of(cfg, sc.cohort, sc.dim)

        if sc.mode == "idle":
            inc = majority(live) or sc.incumbent
            return [{**base, "mode": "explore", "incumbent": inc, "champion": inc,
                     "reason": f"start exploration of {sc.dim} with {inc} as incumbent"}]

        if sc.mode == "paused":
            if hb - (sc.mode_since_hb or hb) < S.SUPERVISOR_PAUSE_H:
                return []
            inc = majority(live) or sc.incumbent
            return [{**base, "mode": "explore", "incumbent": inc, "champion": inc,
                     "reason": f"supervisor pause over; resume exploration around supervisor's choice {inc}"}]

        if sc.mode == "explore":
            start = sc.explore_from_hb if sc.explore_from_hb is not None else hb
            data = arm_data(self.store, wf.id, sc.dim, "all", start, cohorts=cohorts)
            inc = sc.incumbent
            arms = [a for a in self.allowed_arms(wf, sc.dim, sc.excluded) if a != inc]
            n_inc = data.trials(inc, metric)
            res = {a: data.cmp(a, inc, metric) for a in arms}
            enough = {a: res[a].n_a >= S.MIN_N[metric] and n_inc >= S.MIN_N[metric] for a in arms}
            ev = {a: res[a].as_dict() for a in arms}
            winners = []
            for a in arms:
                if enough[a] and res[a].z >= S.Z_WIN:
                    ok, g = self._guard_ok(wf, data, a, inc)
                    ev[a]["guardrail_check"] = g
                    if ok:
                        winners.append(a)
            if winners:
                w = max(winners, key=lambda a: (round(res[a].lcb(), 6), a))
                t = {**base, "mode": "exploit", "champion": w,
                     "reason": f"{w} beats incumbent {inc} on {metric}: {res[w].diff:+.3f} "
                               f"(z={res[w].z:.2f} >= {S.Z_WIN}, n={res[w].n_a}/{res[w].n_b})",
                     "evidence": {"metric": metric, "vs_incumbent": ev, "window_from_hb": start}}
                return [t] + self.segment_overrides(wf, sc, cfg, w, inc, start, metric)
            maxed = arms and all(res[a].n_a >= S.EXPLORE_MAX_N[metric] for a in arms)
            timed_out = (hb - start) >= S.MAX_EXPLORE_HOURS and all(enough.values())
            if maxed or timed_out:
                t = {**base, "mode": "exploit", "champion": inc,
                     "reason": f"no challenger significantly beat {inc} on {metric} "
                               f"({'sample cap reached' if maxed else 'time cap reached'}); keep incumbent",
                     "evidence": {"metric": metric, "vs_incumbent": ev, "window_from_hb": start}}
                return [t] + self.segment_overrides(wf, sc, cfg, inc, inc, start, metric)
            losers = [a for a in arms if enough[a] and res[a].z <= -S.Z_LOSE]
            if losers:
                return [{**base, "mode": "explore", "excluded": sorted(set(sc.excluded) | set(losers)),
                         "reason": f"stop exposing {', '.join(losers)}: significantly worse than {inc} on {metric}",
                         "evidence": {"metric": metric, "vs_incumbent": ev}}]
            return []

        if sc.mode == "exploit":
            return self._check_decay(wf, sc, cfg, hb, cohorts, base)
        return []

    def _check_decay(self, wf: WF, sc: Scope, cfg: dict, hb: int, cohorts: list[str], base: dict) -> list[dict]:
        metric = wf.primary
        c = sc.champion
        others = [a for a in self.allowed_arms(wf, sc.dim, sc.excluded) if a != c]
        if not others:
            return []
        trail_from = max(sc.mode_since_hb or 0, hb - S.DECAY_WINDOW_H)
        data = arm_data(self.store, wf.id, sc.dim, "all", trail_from, cohorts=cohorts)
        pool = data.cmp_vs_pool(c, others, metric)
        ev = {"trailing_from_hb": trail_from, "champion_vs_holdback": pool.as_dict()}
        decayed = pool.n_b >= S.MIN_CTRL_DECAY[metric] and pool.z <= -S.Z_DECAY
        why = f"champion {c} now significantly worse than holdback arms (z={pool.z:.2f})"
        if not decayed and sc.explore_from_hb is not None and sc.mode_since_hb is not None:
            # difference-in-differences: champion's own rate vs the window that promoted it, net of drift
            ref = arm_data(self.store, wf.id, sc.dim, "all", sc.explore_from_hb, hb_to=sc.mode_since_hb,
                           cohorts=cohorts)
            now_c, ref_c = data.rate(c, metric), ref.rate(c, metric)
            n_now, n_ref = data.trials(c, metric), ref.trials(c, metric)
            if None not in (now_c, ref_c) and n_now >= S.MIN_N[metric] and n_ref >= S.MIN_N[metric]:
                drift = 0.0
                tot_now = sum(data.trials(o, metric) for o in others)
                tot_ref = sum(ref.trials(o, metric) for o in others)
                if tot_now >= S.MIN_CTRL_DECAY[metric] and tot_ref >= S.MIN_CTRL_DECAY[metric]:
                    o_now = sum((data.rate(o, metric) or 0) * data.trials(o, metric) for o in others) / tot_now
                    o_ref = sum((ref.rate(o, metric) or 0) * ref.trials(o, metric) for o in others) / tot_ref
                    drift = o_now - o_ref
                did = (now_c - ref_c) - drift
                se = (now_c * (1 - now_c) / n_now + ref_c * (1 - ref_c) / n_ref) ** 0.5 or 1e-9
                ev["did"] = {"champion_now": round(now_c, 4), "champion_ref": round(ref_c, 4),
                             "control_drift": round(drift, 4), "did": round(did, 4), "z": round(did / se, 2)}
                if did <= -S.DID_MIN_DROP and did / se <= -S.Z_DID:
                    decayed = True
                    why = f"champion {c} fell {did:+.3f} vs its promotion window net of control drift"
        if decayed:
            back = sc.incumbent if (sc.incumbent and sc.incumbent != c) else None
            if back is None:
                rates = {o: (data.rate(o, metric) or 0) for o in others}
                back = max(others, key=lambda o: (rates[o], o))
            return [{**base, "mode": "explore", "incumbent": back, "champion": back,
                     "excluded": sorted(set(sc.excluded) | {c}), "rollback": True,
                     "reason": f"DECAY: {why}; roll back to {back} and re-explore", "evidence": ev}]
        # a holdback arm that has since become significantly better than the champion
        since = arm_data(self.store, wf.id, sc.dim, "all", sc.mode_since_hb or 0, cohorts=cohorts)
        best = None
        for o in others:
            r = since.cmp(o, c, metric)
            if r.n_a >= S.MIN_N[metric] and r.n_b >= S.MIN_N[metric] and r.z >= S.Z_WIN:
                ok, _ = self._guard_ok(wf, since, o, c)
                if ok and (best is None or r.lcb() > best[1].lcb()):
                    best = (o, r)
        if best:
            o, r = best
            return [{**base, "mode": "exploit", "champion": o, "incumbent": c,
                     "reason": f"holdback arm {o} now beats champion {c} (z={r.z:.2f})",
                     "evidence": {"vs_champion": r.as_dict()}}]
        return []

    def segment_overrides(self, wf: WF, sc: Scope, cfg: dict, chosen: str, inc: str, start: int,
                          metric: str) -> list[dict]:
        """Heterogeneity: does some segment clearly prefer a different arm than the workflow-level choice?
        Creates at most MAX_COHORTS shared cohort objects, all on ONE feature per workflow so cohorts never
        overlap (a lead matches the first cohort only)."""
        if sc.cohort != "default":
            return []
        existing = self.store.q("SELECT cohort_id, feature, value FROM cohorts WHERE wf=? AND status='active'",
                                (wf.id,))
        if len(existing) >= S.MAX_COHORTS:
            return []
        fixed = self.store.get(f"cohort_feature:{wf.id}")
        feats = [fixed] if fixed else list(FEATURES)
        taken = {(f, v) for _, f, v in existing}
        cands = []
        for f in feats:
            for seg in segments(self.store, wf.id, sc.dim, f, start):
                v = seg.split("=", 1)[1]
                if (f, v) in taken or segment_share(self.store, wf.id, seg, start) < S.MIN_SEG_SHARE:
                    continue
                data = arm_data(self.store, wf.id, sc.dim, seg, start, cohorts=["default"])
                arms = [a for a in self.allowed_arms(wf, sc.dim, sc.excluded) if a != chosen]
                for a in arms:
                    r = data.cmp(a, chosen, metric)
                    if r.n_a >= S.MIN_N_SEG[metric] and r.n_b >= S.MIN_N_SEG[metric] and r.z >= S.Z_SEG:
                        ok, _ = self._guard_ok(wf, data, a, chosen)
                        if ok:
                            cands.append((r.z, f, v, a, r))
        if not cands:
            return []
        by_f: dict[str, float] = {}
        for z, f, *_ in cands:
            by_f[f] = by_f.get(f, 0) + z
        feat = max(by_f, key=lambda f: (by_f[f], f))
        best: dict[str, tuple] = {}
        for z, f, v, a, r in sorted(cands, key=lambda c: (-c[0], c[2], c[3])):
            if f == feat and v not in best:
                best[v] = (a, r)
        out = []
        for v, (a, r) in sorted(best.items())[: S.MAX_COHORTS - len(existing)]:
            cid = f"{feat.split('_')[0]}_{slug(v)}"
            out.append({"wf": wf.id, "cohort": cid, "dim": sc.dim, "mode": "exploit", "champion": a,
                        "incumbent": chosen, "excluded": [],
                        "new_cohort": {"id": cid, "where": {feat: [v]}, "feature": feat, "value": v},
                        "reason": f"segment {feat}={v} prefers {a} over {chosen}: {r.diff:+.3f} (z={r.z:.2f} "
                                  f">= {S.Z_SEG}, n={r.n_a}/{r.n_b})",
                        "evidence": {"segment": f"{feat}={v}", "vs_chosen": r.as_dict()}})
        return out

    # ------------------------------------------------------------------ the look
    def look(self, wf: WF, hb: int) -> None:
        ctl = self.ctl
        if ctl.gov.inflight(wf.id):
            return
        cfg = ctl.live_config(wf.id)["config"]
        self.ensure_scopes(wf, cfg, hb)
        scopes = self.scopes(wf.id)
        transitions: list[dict] = []
        for sc in scopes:
            transitions += self.evaluate(wf, sc, cfg, hb)
        for t in transitions:
            self.store.decide(wf.id, "experiment_eval", t["reason"], t.get("evidence"))
        # target state per scope = transition if any, else current mode
        state = {(s.cohort, s.dim): {"mode": s.mode, "champion": s.champion, "incumbent": s.incumbent,
                                     "excluded": s.excluded} for s in scopes}
        new_cohorts = []
        for t in transitions:
            state[(t["cohort"], t["dim"])] = t
            if t.get("new_cohort"):
                new_cohorts.append(t["new_cohort"])
        new = clone(cfg)
        for nc in new_cohorts:
            new.setdefault("cohorts", []).append({"id": nc["id"], "where": nc["where"]})
        if not ctl.gov.can_request(wf, urgent=False):
            if transitions:
                self.store.decide(wf.id, "deferred", "experiment change wanted but request budget/hours "
                                  "do not allow it now", {"transitions": len(transitions)})
            return
        for (cohort, dim), st in state.items():
            d = self.dist_for(wf, dim, st["mode"], st["champion"], st["incumbent"], st["excluded"])
            if d is None:
                continue
            live = dist_of(new, cohort, dim)
            if cohort != "default" and dim not in (new.get("allocations", {}).get(cohort) or {}):
                live = {}
            if tv_distance(d, live) >= S.MATERIAL_TV if live else True:
                set_dist(new, cohort, dim, d)
        from .wf import changed_dims
        dims = changed_dims(cfg, new)
        if not dims:
            if transitions:  # pure state change, nothing customer-facing moves: no governance needed
                with self.store.tx():
                    self.on_applied(wf.id, transitions, hb, via="no_config_change")
            return
        pkg = self.package(wf, cfg, new, transitions, hb)
        ctl.gov.submit(wf, kind="experiment", new_cfg=new, plan={"transitions": transitions}, urgent=False, **pkg)

    # ------------------------------------------------------------------ proposal package (C10)
    def package(self, wf: WF, cur: dict, new: dict, transitions: list[dict], hb: int) -> dict:
        metric = wf.primary
        gm, _ = wf.guardrail
        window = 72
        scopes_changed = []
        tot_delta = tot_gdelta = 0.0
        affected_share = 0.0
        cohorts_seen = set()
        for c in ["default"] + [x["id"] for x in new.get("cohorts", [])]:
            for dim in ("welcome_message", "voice", "caller_id"):
                a, b = dist_of(cur, c, dim), dist_of(new, c, dim)
                if tv_distance(a, b) < 1e-6:
                    continue
                coh = self.analysis_cohorts(wf.id, c, dim, new)
                data = arm_data(self.store, wf.id, dim, "all", hb - window, cohorts=coh)
                if c != "default" and c not in {x["id"] for x in cur.get("cohorts", [])}:
                    # brand-new cohort: replay on the segment's own history under the default allocation
                    nc = next(x for x in new["cohorts"] if x["id"] == c)
                    (f, vals), = nc["where"].items()
                    data = arm_data(self.store, wf.id, dim, f"{f}={vals[0]}", hb - window, cohorts=["default"])
                    share = segment_share(self.store, wf.id, f"{f}={vals[0]}", hb - window)
                else:
                    from .analysis import cohort_share
                    share = cohort_share(self.store, wf.id, coh, hb - window) or 1.0
                est = {}
                for m in (metric, gm):
                    tot_s = tot_t = 0
                    for arm in data.arms():
                        n, cn, e = data.totals(arm)
                        s, t = (e, cn) if m == "engagement_rate" else (cn, n)
                        tot_s += s
                        tot_t += t
                    mean = tot_s / tot_t if tot_t else 0.0
                    def exp(dist):
                        v = 0.0
                        for arm, w in dist.items():
                            n, cn, e = data.totals(arm)
                            s, t = (e, cn) if m == "engagement_rate" else (cn, n)
                            v += w * shrunk_rate(s, t, mean)
                        return v
                    est[m] = (exp(a), exp(b))
                arms = {arm: dict(zip(("dials", "connected", "engaged"), data.totals(arm))) for arm in data.arms()}
                d_p = est[metric][1] - est[metric][0]
                d_g = est[gm][1] - est[gm][0]
                tot_delta += share * d_p
                tot_gdelta += share * d_g
                if (c, "x") not in cohorts_seen:
                    affected_share += share
                    cohorts_seen.add((c, "x"))
                scopes_changed.append({"cohort": c, "dim": dim, "from": a, "to": b, "traffic_share": round(share, 3),
                                       "arms_observed": arms,
                                       f"expected_{metric}": [round(est[metric][0], 4), round(est[metric][1], 4)],
                                       f"expected_{gm}": [round(est[gm][0], 4), round(est[gm][1], 4)]})
        affected = self.ctl.affected_leads(wf, min(1.0, affected_share or 1.0), hb)
        promos = [t for t in transitions if t["mode"] == "exploit"]
        rollbacks = [t for t in transitions if t.get("rollback")]
        explores = [t for t in transitions if t["mode"] == "explore" and not t.get("rollback")]
        parts = []
        for t in promos:
            where = "" if t["cohort"] == "default" else f" for cohort {t['cohort']}"
            parts.append(f"use {t['dim']} {t['champion']}{where}")
        for t in rollbacks:
            parts.append(f"roll back {t['dim']} to {t['incumbent']} (decay)")
        for t in explores:
            if t.get("excluded") and set(t["excluded"]) != set(self._scope_excl(t)):
                parts.append(f"stop {t['dim']} {','.join(sorted(set(t['excluded']) - set(self._scope_excl(t))))}")
            else:
                parts.append(f"test {t['dim']} options against {t['incumbent']}")
        if not parts:
            parts.append("re-assert experiment allocation (previous request not applied)")
        summary = f"{wf.id}: " + "; ".join(parts)
        if promos or rollbacks:
            impact = max(0.05, min(1.0, abs(tot_delta) / 0.05))
            if rollbacks:
                impact = max(impact, 0.6)
        else:
            impact = 0.2  # exploration: option value, not an expected lift (dry run shows ~0)
        dry_run = {"method": f"replay of the last {window}h of observed per-arm outcomes in the affected cohorts; "
                             "arm rates shrunk to the cohort mean with a 50-call prior; arms never run contribute "
                             "the cohort mean (no extrapolation)",
                   "expected_delta_primary": round(tot_delta, 4), "expected_delta_guardrail": round(tot_gdelta, 4),
                   "primary": metric, "guardrail": gm, "scopes": scopes_changed}
        rationale = " | ".join(t["reason"] for t in transitions) or "restore wanted allocation"
        return {"summary": summary, "affected": affected, "dry_run": dry_run, "impact": round(impact, 3),
                "rationale": rationale, "evidence": {"stats": [t.get("evidence") for t in transitions]}}

    def _scope_excl(self, t: dict) -> list[str]:
        r = self.store.q1("SELECT excluded FROM scopes WHERE wf=? AND cohort=? AND dim=?",
                          (t["wf"], t["cohort"], t["dim"]))
        return json.loads(r[0]) if r and r[0] else []

    # ------------------------------------------------------------------ apply hooks
    def on_applied(self, wf_id: str, transitions: list[dict], hb: int, via: str = "applied") -> None:
        for t in transitions:
            cur = self.store.q1("SELECT mode, explore_from_hb, history FROM scopes WHERE wf=? AND cohort=? AND dim=?",
                                (wf_id, t["cohort"], t["dim"]))
            hist = json.loads(cur[2]) if cur and cur[2] else []
            hist.append({"hb": hb, "mode": t["mode"], "champion": t.get("champion"), "reason": t["reason"][:200]})
            explore_from = cur[1] if cur else None
            if t["mode"] == "explore" and (not cur or cur[0] != "explore" or t.get("rollback")):
                wfo = self.ctl.wfs[wf_id]
                explore_from = hb
                if wfo.next_campaign_only:  # allocations only reach campaigns created after this
                    hours_to_batch = (wfo.batch_hour - hb % 24) % 24 or 24
                    explore_from = hb + hours_to_batch
            self.store.x(
                "INSERT INTO scopes(wf, cohort, dim, mode, champion, incumbent, excluded, mode_since_hb, explore_from_hb,"
                " history, inflight) VALUES(?,?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(wf, cohort, dim) DO UPDATE SET"
                " mode=excluded.mode, champion=excluded.champion, incumbent=excluded.incumbent,"
                " excluded=excluded.excluded, mode_since_hb=excluded.mode_since_hb,"
                " explore_from_hb=excluded.explore_from_hb, history=excluded.history, inflight=NULL",
                (wf_id, t["cohort"], t["dim"], t["mode"], t.get("champion"), t.get("incumbent"),
                 json.dumps(t.get("excluded") or []), hb, explore_from, json.dumps(hist[-30:])))
            if t.get("new_cohort"):
                nc = t["new_cohort"]
                self.store.x("INSERT OR REPLACE INTO cohorts(wf, cohort_id, where_json, feature, value, status, created_sim,"
                             " reason) VALUES(?,?,?,?,?,?,?,?)",
                             (wf_id, nc["id"], json.dumps(nc["where"]), nc["feature"], nc["value"], "active",
                              self.store.sim_ts, t["reason"]))
                self.store.put(f"cohort_feature:{wf_id}", nc["feature"])
            kind = "rollback" if t.get("rollback") else ("promotion" if t["mode"] == "exploit" else "experiment_live")
            self.store.decide(wf_id, kind, f"{t['cohort']}/{t['dim']} -> {t['mode']} "
                              f"(champion {t.get('champion')}) [{via}]: {t['reason']}", t.get("evidence"))

    def on_supervisor_edit(self, wf_id: str, dims: set[str], cfg: dict, hb: int) -> None:
        for sc in self.scopes(wf_id):
            if sc.dim in dims or "cohorts" in dims:
                m = majority(dist_of(cfg, sc.cohort, sc.dim)) or sc.champion
                hist = sc.history + [{"hb": hb, "mode": "paused", "reason": "supervisor edit"}]
                self.store.x("UPDATE scopes SET mode='paused', incumbent=?, champion=?, mode_since_hb=?, history=?"
                             " WHERE wf=? AND cohort=? AND dim=?",
                             (m, m, hb, json.dumps(hist[-30:]), wf_id, sc.cohort, sc.dim))
                self.store.decide(wf_id, "experiment_paused",
                                  f"{sc.key}: supervisor changed {sorted(dims)}; pausing our changes to this dim for "
                                  f"{S.SUPERVISOR_PAUSE_H}h and treating {m} as the incumbent", {"dims": sorted(dims)})
