"""Hallucination incident response (C8): detect → candidates → validate → propose with evidence → verify.

- Detection is population-based: the flagged-call rate of the LIVE prompt over a trailing window must be
  >= HAL_RATIO x the workflow's historical baseline AND significant (one-sided z) with a minimum count.
  The QA flag is noisy (false positives and negatives), which is why we compare against the workflow's own
  historical flag rate rather than against zero.
- Candidates: prompts with production evidence on this workflow (known-good: plenty of connected calls, flag
  rate near baseline) come first, ranked by their observed engagement. Only when none exists do we spend
  3 credits on POST /llm/suggest-fixes. The LLM proposes; it never picks.
- Validation: each candidate (in rank order) gets one validation run; it is accepted only if the Wilson lower
  bound of its pass rate clears the policy's min_validation_pass_rate. The first acceptable candidate wins:
  deterministic given the counts.
- The change goes through /approvals with evidence.validation_run_id (or auto-apply only if a policy
  explicitly allows prompt_version — none of the public ones do).
- Verification: after apply we wait for VERIFY_MIN_CONN connected calls on the new prompt and require its
  flag rate to be back near baseline; otherwise the next candidate is tried.
Every stage is persisted in incidents.detail, so a restart resumes mid-incident without re-spending credits.
"""
from __future__ import annotations

import json

from . import settings as S
from .client import ApiError
from .stats import excess_rate_z, wilson_lower
from .wf import WF, clone


class Incidents:
    def __init__(self, ctl):
        self.ctl = ctl
        self.store = ctl.store

    # ------------------------------------------------------------------ baselines / evidence
    def baseline(self, wf_id: str) -> float:
        b = self.store.get(f"hal_base:{wf_id}")
        if b is None:
            n, x = self.store.q1("SELECT SUM(conn), SUM(hal) FROM prompt_stats WHERE wf=? AND hb<0", (wf_id,))
            b = (x / n) if n and n >= 300 else S.HAL_DEFAULT_BASE
            b = max(b, 0.003)
            self.store.put(f"hal_base:{wf_id}", b)
        return b

    def prompt_evidence(self, wf_id: str) -> dict:
        out = {}
        for p, n, c, e, h in self.store.q("SELECT prompt, SUM(n), SUM(conn), SUM(eng), SUM(hal) FROM prompt_stats "
                                          "WHERE wf=? GROUP BY prompt", (wf_id,)):
            out[p] = {"dials": n, "connected": c, "engagement": round(e / c, 4) if c else None,
                      "halluc_rate": round(h / c, 4) if c else None}
        return out

    def known_good(self, wf: WF, exclude: set[str]) -> list[str]:
        base = self.baseline(wf.id)
        ev = self.prompt_evidence(wf.id)
        good = [p for p, d in ev.items() if p not in exclude and p in wf.options.get("prompt_version", [])
                and (d["connected"] or 0) >= 500 and d["halluc_rate"] is not None and d["halluc_rate"] <= 1.5 * base]
        return sorted(good, key=lambda p: (-(ev[p]["engagement"] or 0), -ev[p]["connected"], p))

    def credits_ok(self, cost: float) -> bool:
        reserve = (self.store.get("credits_start", 0) or 0) * S.CREDIT_RESERVE_FRAC
        return (self.ctl.credits - cost) >= reserve

    # ------------------------------------------------------------------ detection
    def open_incident(self, wf_id: str):
        r = self.store.q1("SELECT id, detail FROM incidents WHERE wf=? AND kind='hallucination' AND status='open' "
                          "ORDER BY id DESC LIMIT 1", (wf_id,))
        return (r[0], json.loads(r[1])) if r else (None, None)

    def detect(self, wf: WF, prompt: str, hb: int) -> None:
        since = self.ctl.prompt_since(wf.id, prompt)
        frm = max(since if since is not None else -10**6, hb - S.HAL_WINDOW_H)
        n, x = self.store.q1("SELECT SUM(conn), SUM(hal) FROM prompt_stats WHERE wf=? AND prompt=? AND hb>=?",
                             (wf.id, prompt, frm))
        n, x = n or 0, x or 0
        base = self.baseline(wf.id)
        if n < S.HAL_MIN_CONN or x < S.HAL_MIN_EVENTS:
            return
        z = excess_rate_z(x, n, base)
        watch = self.ctl.supervisor_touched(wf.id, "prompt_version", hb, hours=12)
        zc = S.Z_HAL_WATCH if watch else S.Z_HAL
        if x / n >= S.HAL_RATIO * base and z >= zc:
            detail = {"stage": "detected", "bad_prompt": prompt, "rate": round(x / n, 4), "baseline": round(base, 4),
                      "n_connected": n, "flagged": x, "z": round(z, 2), "threshold_z": zc, "window_from_hb": frm,
                      "tried": [], "validations": [], "attempts": 0}
            with self.store.tx():
                self.store.x("INSERT INTO incidents(wf, kind, key, status, opened_sim, opened_hb, detail) "
                             "VALUES(?,?,?,?,?,?,?)", (wf.id, "hallucination", prompt, "open", self.store.sim_ts, hb,
                                                       json.dumps(detail)))
                self.store.decide(wf.id, "incident_open",
                                  f"hallucination spike on prompt {prompt}: {x}/{n} = {x / n:.3f} flagged vs baseline "
                                  f"{base:.3f} (z={z:.1f})", detail)
                self.store.alert(wf.id, "page" if x / n >= 3 * base else "ticket", "hallucination_spike",
                                 f"{wf.id}: prompt {prompt} flag rate {x / n:.1%} vs {base:.1%} baseline; controller "
                                 "is validating a fix — approver action needed on the incoming request")

    def _save(self, iid: int, d: dict, status: str = "open") -> None:
        self.store.x("UPDATE incidents SET detail=?, status=?, resolved_sim=CASE WHEN ?='resolved' THEN ? ELSE "
                     "resolved_sim END WHERE id=?", (json.dumps(d), status, status, self.store.sim_ts, iid))

    # ------------------------------------------------------------------ progression
    def progress(self, wf: WF, iid: int, d: dict, hb: int) -> None:
        stage = d["stage"]
        if stage == "detected":
            cands = self.known_good(wf, {d["bad_prompt"], *d["tried"]})
            src = "production evidence"
            if not cands and self.credits_ok(3):
                try:
                    r = self.ctl.client.suggest_fixes(wf.id, f"hallucination flag rate {d['rate']:.1%} on prompt "
                                                             f"{d['bad_prompt']} vs {d['baseline']:.1%} baseline")
                    self.ctl.credits = r.get("credits_remaining", self.ctl.credits)
                    cands = [s["prompt_version"] for s in r.get("suggestions", [])
                             if s["prompt_version"] in wf.options.get("prompt_version", [])
                             and s["prompt_version"] not in {d["bad_prompt"], *d["tried"]}]
                    src = "llm suggest-fixes"
                    with self.store.tx():
                        self.store.decide(wf.id, "llm_suggest", f"LLM suggested {cands} (proposal only)", r)
                except ApiError as e:
                    with self.store.tx():
                        self.store.decide(wf.id, "llm_error", str(e))
            d.update(stage="candidates", candidates=cands, candidate_source=src)
            with self.store.tx():
                self._save(iid, d)
                self.store.decide(wf.id, "incident_candidates", f"candidates {cands} from {src}", None)
            if not cands:
                with self.store.tx():
                    self.store.alert(wf.id, "page", "no_fix_candidate", f"{wf.id}: hallucination incident {iid} has no "
                                     "fix candidate (no known-good prompt, no credits/suggestions) — needs a human")
                return
            stage = "candidates"
        if stage == "candidates":
            for cand in [c for c in d["candidates"] if c not in d["tried"]]:
                if len([v for v in d["validations"]]) >= S.MAX_VALIDATIONS_PER_INCIDENT:
                    break
                cost = -(-S.VALIDATION_N // 100)
                if not self.credits_ok(cost):
                    with self.store.tx():
                        self.store.alert(wf.id, "page", "credits_low", f"{wf.id}: cannot validate fix — credit "
                                         "reserve reached")
                    return
                try:
                    v = self.ctl.client.validate(wf.id, cand, S.VALIDATION_N)
                except ApiError as e:
                    with self.store.tx():
                        self.store.decide(wf.id, "validation_error", str(e))
                    return
                self.ctl.credits = v.get("credits_remaining", self.ctl.credits)
                fails = v.get("failures", round((1 - v["pass_rate"]) * v["n"]))
                lb = wilson_lower(v["n"] - fails, v["n"])
                ok = lb >= wf.min_pass and v["pass_rate"] >= wf.min_pass
                rec = {"prompt": cand, "validation_run_id": v["validation_run_id"], "pass_rate": v["pass_rate"],
                       "n": v["n"], "wilson_lb": round(lb, 4), "accepted": ok}
                d["validations"].append(rec)
                d["tried"].append(cand)
                with self.store.tx():
                    self.store.x("INSERT OR IGNORE INTO validations(validation_run_id, wf, prompt, n, pass_rate, sim_ts,"
                                 " incident_id) VALUES(?,?,?,?,?,?,?)", (v["validation_run_id"], wf.id, cand, v["n"],
                                                                        v["pass_rate"], self.store.sim_ts, iid))
                    self.store.decide(wf.id, "validation", f"{cand}: pass {v['pass_rate']:.3f} (n={v['n']}, "
                                      f"Wilson LB {lb:.3f} vs required {wf.min_pass}) -> "
                                      f"{'ACCEPT' if ok else 'reject'}", rec)
                    if ok:
                        d.update(stage="chosen", chosen=rec)
                    self._save(iid, d)
                if ok:
                    break
            if d["stage"] != "chosen":
                if all(c in d["tried"] for c in d["candidates"]) or len(d["validations"]) >= S.MAX_VALIDATIONS_PER_INCIDENT:
                    with self.store.tx():
                        self.store.alert(wf.id, "page", "no_valid_fix", f"{wf.id}: no candidate prompt passed "
                                         "validation — needs a human decision")
                        d["stage"] = "stuck"
                        self._save(iid, d)
                return
            stage = "chosen"
        if stage == "chosen":
            self._propose(wf, iid, d, hb)
            return
        if stage == "verifying":
            self._verify(wf, iid, d, hb)

    def _propose(self, wf: WF, iid: int, d: dict, hb: int) -> None:
        if self.ctl.gov.inflight(wf.id, "incident"):
            return
        today = self.store.q1("SELECT COUNT(*) FROM proposals WHERE wf=? AND kind='incident' AND sent_hb>=?",
                              (wf.id, hb - 23))[0]
        if today >= S.MAX_PROMPT_FIXES_PER_DAY:
            with self.store.tx():
                self.store.alert(wf.id, "page", "prompt_fight", f"{wf.id}: {today} prompt fixes in 24h — supervisor "
                                 "keeps reintroducing a failing prompt; escalate to the client")
            return
        cur = self.ctl.live_config(wf.id, fresh=True)
        cfg = cur["config"]
        if cfg.get("prompt_version") != d["bad_prompt"]:
            # someone already moved off the bad prompt (e.g. the supervisor): verify that instead
            d.update(stage="verifying", applied_hb=hb, verify_prompt=cfg.get("prompt_version"))
            with self.store.tx():
                self._save(iid, d)
                self.store.decide(wf.id, "incident_note", f"prompt already changed to {cfg.get('prompt_version')}; "
                                  "verifying instead of proposing", None)
            return
        ch = d["chosen"]
        new = clone(cfg)
        new["prompt_version"] = ch["prompt"]
        ev = self.prompt_evidence(wf.id)
        summary = (f"{wf.id}: switch prompt {d['bad_prompt']} -> {ch['prompt']} — hallucination flags "
                   f"{d['rate']:.1%} vs {d['baseline']:.1%} baseline")
        dry = {"method": "production flag/engagement rates per prompt on this workflow + validation run",
               "bad_prompt": {d["bad_prompt"]: ev.get(d["bad_prompt"])}, "fix_prompt": {ch["prompt"]: ev.get(ch["prompt"])},
               "validation": ch, "expected_flag_rate_after": d["baseline"]}
        rationale = (f"Population signal: {d['flagged']}/{d['n_connected']} connected calls flagged on {d['bad_prompt']} "
                     f"(z={d['z']}). Fix chosen from {d.get('candidate_source')}: {ch['prompt']} validated at "
                     f"{ch['pass_rate']:.3f} on n={ch['n']} (Wilson LB {ch['wilson_lb']}). "
                     + ("This reverts a supervisor edit; it is sent for explicit approval, never applied silently."
                        if self.ctl.supervisor_touched(wf.id, "prompt_version", hb, hours=72) else ""))
        aff = self.ctl.affected_leads(wf, 1.0, hb)
        auto = wf.auto_allowed({"prompt_version"})
        pid = self.ctl.gov.submit(wf, kind="incident", new_cfg=new, plan={"incident_id": iid}, urgent=True,
                                  summary=summary, affected=aff, dry_run=dry, impact=0.9, rationale=rationale,
                                  evidence={"validation_run_id": ch["validation_run_id"], "incident_id": iid},
                                  prefer_auto=auto)
        if pid:
            d["attempts"] = d.get("attempts", 0) + 1
            d["stage"] = "proposed"
            d["proposal_id"] = pid
            with self.store.tx():
                self._save(iid, d)

    def _verify(self, wf: WF, iid: int, d: dict, hb: int) -> None:
        p = d.get("verify_prompt") or d["chosen"]["prompt"]
        n, x = self.store.q1("SELECT SUM(conn), SUM(hal) FROM prompt_stats WHERE wf=? AND prompt=? AND hb>=?",
                             (wf.id, p, d["applied_hb"]))
        n, x = n or 0, x or 0
        if n < S.VERIFY_MIN_CONN:
            return
        base = self.baseline(wf.id)
        z = excess_rate_z(x, n, base)
        with self.store.tx():
            if z < 2.0:
                d.update(stage="resolved", verify={"prompt": p, "n": n, "flagged": x, "rate": round(x / n, 4), "z": round(z, 2)})
                self._save(iid, d, status="resolved")
                self.store.decide(wf.id, "incident_resolved", f"prompt {p}: {x}/{n} flagged ({x / n:.3f}) — back at "
                                  f"baseline {base:.3f}; incident closed", d["verify"])
            else:
                d["tried"].append(p)
                d.update(stage="detected", verify_failed={"prompt": p, "n": n, "flagged": x, "z": round(z, 2)})
                d["bad_prompt"] = p
                self._save(iid, d)
                self.store.decide(wf.id, "incident_verify_failed", f"prompt {p} still flagged at {x / n:.3f}; trying "
                                  "next candidate", None)

    # ------------------------------------------------------------------ hooks from governance
    def on_applied(self, iid: int, hb: int) -> None:
        r = self.store.q1("SELECT detail FROM incidents WHERE id=?", (iid,))
        if not r:
            return
        d = json.loads(r[0])
        d.update(stage="verifying", applied_hb=hb, verify_prompt=d["chosen"]["prompt"])
        self._save(iid, d)

    def on_failed(self, iid: int, status: str) -> None:
        r = self.store.q1("SELECT detail FROM incidents WHERE id=?", (iid,))
        if not r:
            return
        d = json.loads(r[0])
        if d.get("stage") == "proposed":
            d["stage"] = "chosen"  # validation evidence is still valid: re-propose on the current config
            d.setdefault("failed_proposals", []).append(status)
            self._save(iid, d)

    # ------------------------------------------------------------------ tick
    def tick(self, hb: int, dialling: bool) -> None:
        for wf in self.ctl.wfs.values():
            iid, d = self.open_incident(wf.id)
            if iid:
                if d["stage"] != "stuck":
                    self.progress(wf, iid, d, hb)
                continue
            if dialling:
                prompt = self.ctl.live_config(wf.id)["config"].get("prompt_version")
                if prompt:
                    self.detect(wf, prompt, hb)
