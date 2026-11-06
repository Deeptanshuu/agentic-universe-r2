"""Read-only views over the store: the audit trail (C12), the supervisor screen (C11, CLI) and loop health
(C14). Works on a live or finished run; opens the SQLite file read-only so it can run beside the loop.

    python -m controller why    --db state/public_02_incidents.sqlite --wf ks_cancel --at "D3 14:00"
    python -m controller status --db state/public_02_incidents.sqlite
    python -m controller log    --db state/public_02_incidents.sqlite --wf nw_renewal --kind proposal
    python -m controller health --db state/public_02_incidents.sqlite
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3

from .analysis import kpis


class RO:
    def __init__(self, path: str):
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    def q(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def q1(self, sql, args=()):
        return self.db.execute(sql, args).fetchone()

    def get(self, k, default=None):
        r = self.q1("SELECT v FROM meta WHERE k=?", (k,))
        return json.loads(r[0]) if r else default


def parse_at(db: RO, at: str) -> tuple[int, str]:
    """'D3 14:00' or an ISO timestamp -> (hour bucket, iso)."""
    d0 = dt.datetime.fromisoformat(db.get("d0"))
    m = re.match(r"^D(\d+)\s+(\d{1,2}):(\d{2})$", at.strip())
    if m:
        t = d0 + dt.timedelta(days=int(m[1]), hours=int(m[2]), minutes=int(m[3]))
    else:
        t = dt.datetime.fromisoformat(at)
    mins = int((t - d0).total_seconds() // 60)
    return mins // 60, t.isoformat()


def fmt_hb(hb: int | None) -> str:
    if hb is None:
        return "-"
    return f"D{hb // 24} {hb % 24:02d}:00"


def why(path: str, wf: str, at: str) -> str:
    db = RO(path)
    hb, iso = parse_at(db, at)
    out = [f"# Why is {wf} configured the way it is at {at} ({iso})?", ""]
    v = db.q1("SELECT version, actor, changed_dims, proposal_id, approval_id, applied_at, hb, config FROM config_versions"
              " WHERE wf=? AND applied_at IS NOT NULL AND applied_at<=? ORDER BY version DESC LIMIT 1", (wf, iso))
    if not v:
        out.append("Running the business default config (version 1); the controller had not changed it yet.")
        return "\n".join(out)
    version, actor, dims, pid, aid, applied_at, vhb, cfg = v
    out.append(f"Active config: v{version}, set at {applied_at} by **{actor}**, changed {json.loads(dims or '[]')}")
    if actor == "supervisor":
        out.append("This was a client supervisor edit (bypasses governance). The controller recorded it and did not "
                   "revert it silently.")
    if pid:
        p = db.q1("SELECT channel, kind, status, summary, body, approval_id, sent_sim, decided_sim, applied_sim, reasons "
                  "FROM proposals WHERE proposal_id=?", (pid,))
        if p:
            channel, kind, status, summary, body, aid2, sent, decided, applied, reasons = p
            b = json.loads(body)
            out += ["", f"## The change: proposal `{pid}` ({kind}, via {channel})",
                    f"- summary: {summary}"]
            if channel == "approval":
                out.append(f"- requested {sent}; approval `{aid2}` approved at {decided} by the {wf} approver "
                           f"(simulated client approver; the API does not expose a name); applied {applied}")
            else:
                out.append(f"- auto-applied at {applied} under the workflow's auto_apply policy")
            out.append(f"- diff: {json.dumps(b.get('diff') or {}, sort_keys=True)}")
            if "impact_score" in b:
                out.append(f"- impact_score {b['impact_score']}, affected_leads {b['affected_leads']}")
            out.append(f"- rationale: {b.get('rationale')}")
            dr = b.get("dry_run") or {}
            if dr:
                keep = {k: dr[k] for k in dr if k in ("method", "expected_delta_primary", "expected_delta_guardrail",
                                                     "validation", "incidents")}
                out.append(f"- dry run: {json.dumps(keep, default=str)[:900]}")
            if b.get("evidence"):
                out.append(f"- evidence: {json.dumps(b['evidence'], default=str)[:900]}")
    out += ["", "## Decisions that led here (48h before)"]
    for sim_ts, kind, summary in db.q("SELECT sim_ts, kind, summary FROM decisions WHERE wf=? AND hb BETWEEN ? AND ? "
                                      "AND kind NOT IN ('daily_summary','deferred','look') ORDER BY id", (wf, vhb - 48, vhb)):
        out.append(f"- {sim_ts[5:16]} [{kind}] {summary}")
    out += ["", "## What happened next"]
    before = kpis(_StoreShim(db), wf, vhb - 24, vhb)
    after = kpis(_StoreShim(db), wf, vhb + 1, vhb + 25)
    out.append(f"- 24h before: pickup {before['pickup_rate']} engagement {before['engagement_rate']} "
               f"flag rate {before['halluc_rate']} (dials {before['dials']})")
    out.append(f"- 24h after:  pickup {after['pickup_rate']} engagement {after['engagement_rate']} "
               f"flag rate {after['halluc_rate']} (dials {after['dials']})")
    for sim_ts, kind, summary in db.q("SELECT sim_ts, kind, summary FROM decisions WHERE wf=? AND hb > ? AND hb <= ? "
                                      "AND kind NOT IN ('daily_summary','deferred','experiment_eval','look') ORDER BY id LIMIT 15",
                                      (wf, vhb, vhb + 48)):
        out.append(f"- {sim_ts[5:16]} [{kind}] {summary}")
    return "\n".join(out)


class _StoreShim:
    def __init__(self, db):
        self.db = db

    def q1(self, sql, args=()):
        return self.db.q1(sql, args)


def status(path: str, wf_filter: str | None = None) -> str:
    """The one screen a supervisor asked for: tested / winning / waiting on you / changed today."""
    db = RO(path)
    sim_min = db.get("sim_minute", 0)
    hb = sim_min // 60
    out = [f"# Controller status at {fmt_hb(hb)} (sim minute {sim_min}) — credits {_credits(db)}", ""]
    for (wf,) in db.q("SELECT wf FROM workflows ORDER BY wf"):
        if wf_filter and wf != wf_filter:
            continue
        spec = json.loads(db.q1("SELECT spec FROM workflows WHERE wf=?", (wf,))[0])
        k = kpis(_StoreShim(db), wf, hb - 24, hb)
        out.append(f"## {wf}  (primary {spec['objective']['primary']}, guardrail {spec['objective'].get('guardrail')})")
        out.append(f"last 24h: pickup {k['pickup_rate']} · engagement {k['engagement_rate']} · flag rate "
                   f"{k['halluc_rate']} · dials {k['dials']}")
        out.append("being tested / winning:")
        for cohort, dim, mode, champ, inc, excl in db.q("SELECT cohort, dim, mode, champion, incumbent, excluded FROM "
                                                        "scopes WHERE wf=? ORDER BY cohort, dim", (wf,)):
            ex = json.loads(excl or "[]")
            out.append(f"  - {cohort}/{dim}: {mode}" + (f", champion {champ}" if mode == "exploit" else
                                                       f", incumbent {inc}") + (f", stopped {ex}" if ex else ""))
        pend = db.q("SELECT proposal_id, status, summary, approval_id FROM proposals WHERE wf=? AND status IN "
                    "('intent','pending','approved','applying')", (wf,))
        out.append("waiting on you:" + ("" if pend else " nothing"))
        for pid, st, summ, aid in pend:
            out.append(f"  - {aid or pid} [{st}] {summ}")
        out.append("changed today:")
        any_ = False
        for sim_ts, kind, summ in db.q("SELECT sim_ts, kind, summary FROM decisions WHERE wf=? AND hb>=? AND kind IN "
                                       "('applied','supervisor_edit','incident_open','incident_resolved','rollback',"
                                       "'promotion','rejected','expired','stale')", (wf, hb - hb % 24)):
            out.append(f"  - {sim_ts[11:16]} [{kind}] {summ}")
            any_ = True
        if not any_:
            out.append("  - nothing")
        inc = db.q("SELECT kind, key, status, opened_sim, resolved_sim FROM incidents WHERE wf=? ORDER BY id", (wf,))
        if inc:
            out.append("incidents:")
            for kind, key, st, o, r in inc:
                out.append(f"  - {kind} {key}: {st} (opened {o[5:16]}{', resolved ' + r[5:16] if r else ''})")
        out.append("")
    al = db.q("SELECT sim_ts, severity, wf, message FROM alerts ORDER BY id DESC LIMIT 10")
    if al:
        out.append("## Alerts")
        out += [f"- {t[5:16]} [{s}] {m}" for t, s, w, m in al]
    return "\n".join(out)


def _credits(db: RO):
    r = db.q1("SELECT credits FROM loop_metrics ORDER BY sim_minute DESC LIMIT 1")
    return r[0] if r else "?"


def decisions(path: str, wf: str | None, kind: str | None, limit: int) -> str:
    db = RO(path)
    sql = "SELECT sim_ts, wf, kind, summary, proposal_id, approval_id FROM decisions WHERE 1=1"
    args: list = []
    if wf:
        sql += " AND wf=?"
        args.append(wf)
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = db.q(sql, args)[::-1]
    return "\n".join(f"{t[5:16]} {w or '-':11s} {k:20s} {s}" + (f"  ({p}{' / ' + a if a else ''})" if p else "")
                     for t, w, k, s, p, a in rows)


def loop_health(path: str) -> str:
    db = RO(path)
    rows = db.q("SELECT sim_minute, wall, events, ingest_secs, decide_secs, credits, rss_mb, pending FROM loop_metrics "
                "ORDER BY sim_minute")
    if not rows:
        return "no metrics yet"
    out = ["day | events | ingest s | decide s | wall s | ev/s | peak RSS MB | credits | approvals pending"]
    by = {}
    for m, wall, ev, isec, dsec, cr, rss, pend in rows:
        d = (m - 1) // 1440
        b = by.setdefault(d, [0, 0.0, 0.0, None, None, 0.0, cr, 0])
        b[0] += ev or 0
        b[1] += isec or 0
        b[2] += dsec or 0
        b[3] = wall if b[3] is None else min(b[3], wall)
        b[4] = wall if b[4] is None else max(b[4], wall)
        b[5] = max(b[5], rss or 0)
        b[6] = cr
        b[7] = max(b[7], pend or 0)
    for d, (ev, isec, dsec, w0, w1, rss, cr, pend) in sorted(by.items()):
        out.append(f"D{d} | {ev} | {isec:.1f} | {dsec:.1f} | {w1 - w0:.0f} | {ev / isec if isec else 0:.0f} | "
                   f"{rss:.0f} | {cr} | {pend}")
    al = db.q("SELECT severity, COUNT(*) FROM alerts GROUP BY severity")
    out.append(f"alerts: {dict(al)}")
    return "\n".join(out)
