"""Event ingestion (C1): stream a chunk, aggregate, commit aggregates + watermark atomically.

Idempotency: a chunk's aggregates and its row in `chunks` are written in one SQLite transaction. If the
process dies mid-chunk nothing is committed and the chunk is simply re-read on restart; if it dies after
the commit, the watermark says the chunk is done and it is skipped. Either way every event is counted
exactly once.

We never keep per-call rows: calls are folded into hourly aggregates keyed by exactly the dimensions the
decision logic needs (see store.SCHEMA). Lead features arrive in `lead` events and are joined to calls in
memory (only leads of still-active campaigns are kept in RAM; all are persisted for restart).
"""
from __future__ import annotations

import datetime as dt
import json
import time
from collections import defaultdict

import httpx

FEATURES = ("lang", "city_tier", "age_band", "value_band")
EXPERIMENT_DIMS = ("welcome_message", "voice")


class TimeBase:
    """Maps IST timestamps to hour buckets relative to D0 00:00 (negative for history)."""

    def __init__(self, d0_iso: str):
        self.d0 = dt.date.fromisoformat(d0_iso[:10])
        self._cache: dict[str, int] = {}

    def day(self, ts: str) -> int:
        d = self._cache.get(ts[:10])
        if d is None:
            d = (dt.date.fromisoformat(ts[:10]) - self.d0).days
            self._cache[ts[:10]] = d
        return d

    def hb(self, ts: str) -> int:
        return self.day(ts) * 24 + int(ts[11:13])


class LeadIndex:
    """lead_id -> tuple of segment labels ('all', 'lang=hi', ...). Tuples are interned (shared)."""

    def __init__(self):
        self.map: dict = {}
        self.day_of: dict = {}  # key -> campaign day, for eviction
        self._segs: dict[tuple, tuple] = {}
        self._wf_idx: dict[str, int] = {}

    def key(self, lead_id: str):
        wf, _, rest = lead_id.partition(":")
        if rest[:1] == "l" and rest[1:].isdigit():
            i = self._wf_idx.setdefault(wf, len(self._wf_idx) + 1)
            return (i << 40) | int(rest[1:])
        return lead_id

    def segs_for(self, fstr: str) -> tuple:
        t = self._segs.get(fstr)  # fstr = "hi|2|31-45|low"
        if t is None:
            vals = fstr.split("|")
            t = ("all",) + tuple(f"{k}={v}" for k, v in zip(FEATURES, vals))
            self._segs[fstr] = t
        return t

    def add(self, lead_id: str, fstr: str, day: int) -> None:
        k = self.key(lead_id)
        self.map[k] = self.segs_for(fstr)
        self.day_of[k] = day

    def get(self, lead_id: str):
        return self.map.get(self.key(lead_id))

    def evict_before(self, day: int) -> int:
        dead = [k for k, d in self.day_of.items() if d < day]
        for k in dead:
            self.map.pop(k, None)
            self.day_of.pop(k, None)
        return len(dead)


def fstr_of(features: dict) -> str:
    return "|".join(str(features.get(k, "?")) for k in FEATURES)


class Accumulator:
    """Scratch aggregation for one chunk; discarded if anything fails before commit."""

    def __init__(self, tb: TimeBase, leads: LeadIndex, first_attempt: dict, keep_days: int = 4):
        self.tb, self.leads, self.first_attempt = tb, leads, first_attempt
        self.keep_days = keep_days
        self.max_day = None
        self.arm = defaultdict(lambda: [0, 0, 0])
        self.kpi = defaultdict(lambda: [0, 0, 0, 0, 0])
        self.prompt = defaultdict(lambda: [0, 0, 0, 0])
        self.cid = defaultdict(lambda: [0, 0])
        self.new_leads: list = []
        self.campaigns: list = []
        self.controls: list = []
        self.events = 0
        self.calls = 0

    def feed(self, line) -> None:
        e = json.loads(line)
        self.events += 1
        t = e["type"]
        if t == "call":
            self._call(e)
        elif t == "lead":
            day = self.tb.day(e["ts"])
            if self.max_day is None or day > self.max_day:
                # campaigns only dial for len(cadence) days: older leads can't be called again
                self.max_day = day
                self.leads.evict_before(day - self.keep_days)
            fs = fstr_of(e["features"])
            self.leads.add(e["lead_id"], fs, day)
            self.new_leads.append((e["lead_id"], e["workflow_id"], day, fs))
        elif t == "campaign_created":
            self.campaigns.append((e["campaign_id"], e["workflow_id"], self.tb.day(e["ts"]), e["leads"],
                                   e.get("config_version_at_creation")))
        else:  # config_changed, approval_decided, anything new: keep for the control plane
            self.controls.append(e)

    def _call(self, e: dict) -> None:
        self.calls += 1
        wf = e["workflow_id"]
        ts = e["ts"]
        hb = self.tb.hb(ts)
        v = e["variant"]
        conn = 1 if e["connected"] else 0
        eng = 1 if e["engaged"] else 0
        qa = e.get("qa")
        hal = 1 if (qa and qa.get("hallucination_flag")) else 0
        ver = e.get("config_version", 0)
        coh = e.get("cohort_id") or "default"
        fa = self.first_attempt.get(wf)
        uniq = 1 if (fa and e.get("attempt_no") == fa.get(e.get("campaign_day_index"))) else 0

        k = self.kpi[(wf, hb, coh)]
        k[0] += 1; k[1] += conn; k[2] += eng; k[3] += hal; k[4] += uniq  # noqa: E702
        p = self.prompt[(wf, v["prompt_version"], hb)]
        p[0] += 1; p[1] += conn; p[2] += eng; p[3] += hal  # noqa: E702
        c = self.cid[(wf, v["caller_id"], hb)]
        c[0] += 1; c[1] += conn  # noqa: E702

        segs = self.leads.get(e["lead_id"]) or ("all",)
        arm = self.arm
        for dim in EXPERIMENT_DIMS:
            a = v[dim]
            for seg in segs:
                s = arm[(wf, dim, seg, hb, ver, coh, a)]
                s[0] += 1; s[1] += conn; s[2] += eng  # noqa: E702
        s = arm[(wf, "caller_id", "all", hb, ver, coh, v["caller_id"])]
        s[0] += 1; s[1] += conn; s[2] += eng  # noqa: E702

    def flush_leads(self, db) -> None:
        db.executemany("INSERT OR IGNORE INTO leads(lead_id, wf, day, f) VALUES(?,?,?,?)", self.new_leads)
        self.new_leads = []

    def flush(self, db) -> None:
        db.executemany(
            "INSERT INTO arm_stats(wf,dim,seg,hb,ver,cohort,arm,n,conn,eng) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT DO UPDATE SET n=n+excluded.n, conn=conn+excluded.conn, eng=eng+excluded.eng",
            [k + tuple(v) for k, v in self.arm.items()])
        db.executemany(
            "INSERT INTO kpi_stats(wf,hb,cohort,n,conn,eng,hal,uniq) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT DO UPDATE SET n=n+excluded.n, conn=conn+excluded.conn, eng=eng+excluded.eng, "
            "hal=hal+excluded.hal, uniq=uniq+excluded.uniq",
            [k + tuple(v) for k, v in self.kpi.items()])
        db.executemany(
            "INSERT INTO prompt_stats(wf,prompt,hb,n,conn,eng,hal) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT DO UPDATE SET n=n+excluded.n, conn=conn+excluded.conn, eng=eng+excluded.eng, "
            "hal=hal+excluded.hal",
            [k + tuple(v) for k, v in self.prompt.items()])
        db.executemany(
            "INSERT INTO cid_stats(wf,cid,hb,n,conn) VALUES(?,?,?,?,?) "
            "ON CONFLICT DO UPDATE SET n=n+excluded.n, conn=conn+excluded.conn",
            [k + tuple(v) for k, v in self.cid.items()])
        self.flush_leads(db)
        db.executemany("INSERT OR IGNORE INTO campaigns(campaign_id, wf, day, leads, ver_at_creation) "
                       "VALUES(?,?,?,?,?)", self.campaigns)


class Ingestor:
    def __init__(self, store, client, tb: TimeBase, first_attempt: dict, on_control, keep_days: int = 4):
        self.store, self.client, self.tb = store, client, tb
        self.keep_days = keep_days
        self.first_attempt = first_attempt
        self.leads = LeadIndex()
        self.on_control = on_control  # called inside the chunk transaction with each control event
        self.total_events = 0
        self.total_secs = 0.0

    def watermark(self) -> int:
        r = self.store.q1("SELECT MAX(chunk) FROM chunks")
        return -1 if r[0] is None else r[0]

    def reload_leads(self, min_day: int) -> int:
        n = 0
        for lead_id, day, f in self.store.q("SELECT lead_id, day, f FROM leads WHERE day >= ?", (min_day,)):
            self.leads.add(lead_id, f, day)
            n += 1
        return n

    def pending_chunks(self) -> list[dict]:
        wm = self.watermark()
        out = []
        for kind in ("history", "events"):
            out += [c for c in self.client.list_chunks(kind) if c["chunk"] > wm]
        return sorted(out, key=lambda c: c["chunk"])

    def ingest(self, meta: dict) -> tuple[int, float]:
        n = meta["chunk"]
        if self.store.q1("SELECT 1 FROM chunks WHERE chunk=?", (n,)):
            return 0, 0.0
        for attempt in range(5):
            t0 = time.perf_counter()
            acc = Accumulator(self.tb, self.leads, self.first_attempt, self.keep_days)
            try:
                with self.store.tx() as db:
                    for line in self.client.stream_chunk(n):
                        acc.feed(line)
                        if len(acc.new_leads) >= 50000:
                            acc.flush_leads(db)
                    acc.flush(db)
                    for ev in acc.controls:
                        self.on_control(ev)
                    secs = time.perf_counter() - t0
                    db.execute("INSERT INTO chunks(chunk, kind, events, last_event_id, until_minute,"
                               " ingested_wall, ingest_secs) VALUES(?,?,?,?,?,?,?)",
                               (n, meta.get("kind"), acc.events, meta.get("last_event_id"),
                                meta.get("until_minute"), time.time(), secs))
            except (httpx.TransportError, ConnectionError):
                # transport hiccup: the transaction rolled back, re-read the whole chunk
                if attempt == 4:
                    raise
                time.sleep(1 + attempt)
                continue
            self.total_events += acc.events
            self.total_secs += secs
            return acc.events, secs
        return 0, 0.0
