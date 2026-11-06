"""The controller: one process, one SQLite file, one loop.

    advance 15 min ─▶ ingest new chunks (aggregates + watermark, atomically)
                   ─▶ fold config_changed / approval_decided into the store (same transaction)
                   ─▶ governance tick: poll approvals, apply approved ones, reconcile crashes
                   ─▶ caller-ID health: detect + rotate          (every tick)
                   ─▶ hallucination incidents: detect + respond (every tick)
                   ─▶ experiments: evaluate + propose           (at the fixed looks, 11:00 and 15:00)

Restart = run the same command again. Setup reconciles every proposal whose outcome wasn't recorded,
re-ingests any chunk past the watermark and continues at whatever minute the simulator is at.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import resource
import sys
import time

from . import settings as S
from .analysis import kpis
from .client import ApiError, SimClient
from .experiments import Experiments
from .governance import Governance
from .health import Health
from .incidents import Incidents
from .ingest import Ingestor, TimeBase
from .store import Store
from .wf import WF, majority, dist_of

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(msg: str) -> None:
    print(f"[controller {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


class Controller:
    def __init__(self, url: str, state_dir: str | None = None, max_ticks: int | None = None):
        self.url = url
        self.state_dir = state_dir or os.environ.get("CONTROLLER_STATE_DIR") or os.path.join(REPO, "state")
        self.client = SimClient(url)
        self.max_ticks = max_ticks
        self.hb = 0
        self.hour = 0
        self.minute = 0
        self.credits = 0.0
        self._cfg_cache: dict[str, dict] = {}
        self.wfs: dict[str, WF] = {}

    # ------------------------------------------------------------------ setup
    def _wait_for_sim(self) -> dict:
        t0 = time.time()
        while True:
            try:
                return self.client.state()
            except Exception as e:  # noqa: BLE001
                if time.time() - t0 > 300:
                    raise
                log(f"waiting for simulator at {self.url}: {e}")
                time.sleep(2)

    def setup(self) -> None:
        st = self._wait_for_sim()
        os.makedirs(self.state_dir, exist_ok=True)
        path = os.path.join(self.state_dir, f"{st['scenario']}.sqlite")
        self._lock = open(path + ".lock", "w")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"another controller holds {path}.lock")
        self.store = Store(path)
        world = self.store.get("world")
        seen_min = self.store.get("sim_minute", 0) or 0
        last_ev = self.store.q1("SELECT MAX(last_event_id) FROM chunks")[0] or 0
        if world and (world.get("scenario") != st["scenario"] or st["sim_minute"] < seen_min
                      or st.get("last_event_id", 0) < last_ev or world.get("end_minute") != st["end_minute"]):
            # The simulator restarted (new world). Our memory belongs to the old one: archive it.
            self.store.db.close()
            old = f"{path}.{int(time.time())}.old"
            for suf in ("", "-wal", "-shm"):
                if os.path.exists(path + suf):
                    os.rename(path + suf, old + suf)
            log(f"simulator world changed; archived previous state to {old}")
            self.store = Store(path)
            world = None
        resumed = world is not None
        with self.store.tx():
            self.store.put("world", {"scenario": st["scenario"], "end_minute": st["end_minute"], "url": self.url})
            if self.store.get("credits_start") is None:
                self.store.put("credits_start", st["credits_remaining"])
            if self.store.get("d0") is None:
                t = dt.datetime.fromisoformat(st["ts"]) - dt.timedelta(minutes=st["sim_minute"])
                self.store.put("d0", t.isoformat())
            starts = (self.store.get("starts", 0) or 0) + 1
            self.store.put("starts", starts)
        self.set_time(st)
        for w in self.client.workflows():
            self.wfs[w["workflow_id"]] = WF(w["workflow_id"], w)
        with self.store.tx():
            for w in self.wfs.values():
                self.store.x("INSERT OR REPLACE INTO workflows(wf, tenant, spec) VALUES(?,?,?)",
                             (w.id, w.tenant, json.dumps(w.spec)))
            self.store.decide(None, "controller_start", f"start #{starts} ({'resume' if resumed else 'fresh'}) at "
                              f"{st['ts']} minute {st['sim_minute']}", {"url": self.url})
        self.tb = TimeBase(self.store.get("d0"))
        self.keep_days = max(len(w.cadence) for w in self.wfs.values()) + 1
        self.ingestor = Ingestor(self.store, self.client, self.tb,
                                 {w.id: w.first_attempt_map() for w in self.wfs.values()}, self.on_control,
                                 keep_days=self.keep_days)
        self.gov = Governance(self)
        self.exp = Experiments(self)
        self.health = Health(self)
        self.inc = Incidents(self)
        n = self.ingestor.reload_leads(self.hb // 24 - self.keep_days)
        if resumed:
            log(f"resuming at {st['ts']}: reloaded {n} active leads; reconciling proposals")
        self.gov.reconcile()
        self.ingest_pending()
        self.process_config_changes()
        with self.store.tx():
            for w in self.wfs.values():
                self.exp.ensure_scopes(w, self.live_config(w.id)["config"], self.hb)
                self.inc.baseline(w.id)

    # ------------------------------------------------------------------ helpers used by modules
    def set_time(self, st: dict) -> None:
        self.minute = st["sim_minute"]
        self.hb = self.minute // 60
        self.hour = self.hb % 24
        self.store.sim_ts = st["ts"]
        self.store.hb = self.hb
        if "credits_remaining" in st:
            self.credits = st["credits_remaining"]

    def live_config(self, wf_id: str, fresh: bool = False) -> dict:
        if fresh or wf_id not in self._cfg_cache:
            self._cfg_cache[wf_id] = self.client.config(wf_id)
        return self._cfg_cache[wf_id]

    def invalidate(self, wf_id: str | None = None) -> None:
        if wf_id:
            self._cfg_cache.pop(wf_id, None)
        else:
            self._cfg_cache.clear()

    def affected_leads(self, wf: WF, share: float, hb: int) -> int:
        if wf.next_campaign_only:
            base = wf.batch_size  # allocation changes only reach the next campaign
        else:
            base = self.store.q1("SELECT SUM(uniq) FROM kpi_stats WHERE wf=? AND hb>=?", (wf.id, hb - 24))[0] or 0
            base = base or wf.batch_size
        return int(round(base * max(0.0, min(1.0, share))))

    def supervisor_touched(self, wf_id: str, dim: str, hb: int, hours: int = S.SUPERVISOR_PAUSE_H) -> bool:
        for (dims,) in self.store.q("SELECT changed_dims FROM config_versions WHERE wf=? AND actor='supervisor' "
                                    "AND hb>=?", (wf_id, hb - hours)):
            if dim in json.loads(dims or "[]"):
                return True
        return False

    def prompt_since(self, wf_id: str, prompt: str) -> int | None:
        r = self.store.q1("SELECT hb FROM config_versions WHERE wf=? AND changed_dims LIKE '%prompt_version%' "
                          "ORDER BY version DESC LIMIT 1", (wf_id,))
        return r[0] if r else None

    # ------------------------------------------------------------------ control events
    def on_control(self, ev: dict) -> None:
        """Runs INSIDE the chunk transaction, so control events are applied exactly once."""
        t = ev.get("type")
        if t == "config_changed":
            self.store.x(
                "INSERT INTO config_versions(wf, version, actor, changed_dims, proposal_id, approval_id, applied_at, hb,"
                " config) VALUES(?,?,?,?,?,?,?,?,NULL) ON CONFLICT(wf, version) DO UPDATE SET actor=excluded.actor,"
                " changed_dims=excluded.changed_dims, applied_at=excluded.applied_at, hb=excluded.hb",
                (ev["workflow_id"], ev["version"], ev.get("actor"), json.dumps(ev.get("changed_dims") or []),
                 ev.get("proposal_id"), ev.get("approval_id"), ev.get("ts"), self.tb.hb(ev["ts"])))
            if ev.get("actor") == "supervisor":
                self.store.put(f"sup_pending:{ev['workflow_id']}:{ev['version']}", True)
        elif t == "approval_decided":
            self.gov.on_decided_event(ev)

    def process_config_changes(self) -> None:
        rows = self.store.q("SELECT wf, version, actor, changed_dims, hb FROM config_versions WHERE config IS NULL "
                            "ORDER BY wf, version")
        for wf_id, version, actor, dims, hb in rows:
            cur = self.live_config(wf_id, fresh=True)
            cfg = cur["config"] if cur["version"] == version else None
            dset = set(json.loads(dims or "[]"))
            with self.store.tx():
                self.store.x("UPDATE config_versions SET config=? WHERE wf=? AND version=?",
                             (json.dumps(cfg) if cfg is not None else "{}", wf_id, version))
                if actor == "supervisor" and self.store.get(f"sup_pending:{wf_id}:{version}"):
                    self.store.put(f"sup_pending:{wf_id}:{version}", False)
                    self.store.decide(wf_id, "supervisor_edit", f"supervisor changed {sorted(dset)} -> v{version}; "
                                      "we adopt it as the new base and do not revert it silently",
                                      {"config": cfg}, version=version)
                    self.exp.on_supervisor_edit(wf_id, dset, cur["config"], hb)
                    pend = self.gov.inflight(wf_id)
                    if pend:
                        self.store.decide(wf_id, "note", f"{len(pend)} in-flight proposal(s) are now based on an old "
                                          "version; they will be re-planned on the new config if approved", None)

    def on_proposal_applied(self, p: dict, version: int) -> None:
        if p["kind"] == "experiment":
            self.exp.on_applied(p["wf"], p["plan"].get("transitions", []), self.hb)
        elif p["kind"] == "incident":
            self.inc.on_applied(p["plan"]["incident_id"], self.hb)

    def on_proposal_failed(self, p: dict, status: str) -> None:
        if p["kind"] == "incident" and "incident_id" in p["plan"]:
            self.inc.on_failed(p["plan"]["incident_id"], status)

    # ------------------------------------------------------------------ the loop
    def ingest_pending(self, hint=None) -> tuple[int, float]:
        ev = secs = 0
        chunks = hint if hint else self.ingestor.pending_chunks()
        wm = self.ingestor.watermark()
        for c in sorted(chunks, key=lambda c: c["chunk"]):
            if c["chunk"] <= wm:
                continue
            e, s = self.ingestor.ingest(c)
            ev += e
            secs += s
            if c.get("kind") == "history":
                log(f"ingested history chunk {c['chunk']}: {e} events in {s:.1f}s")
        return ev, secs

    def next_step(self, st: dict) -> int:
        m, end = st["sim_minute"], st["end_minute"]
        hour = (m // 60) % 24
        lo = min(min(w.window[0], w.approver_hours[0], w.batch_hour) for w in self.wfs.values())
        hi = max(max(w.window[1], w.approver_hours[1] + int(w.gov.get("apply_window_hours", 2))) for w in self.wfs.values())
        busy = self.store.q1("SELECT COUNT(*) FROM proposals WHERE status IN ('approved','applying','intent')")[0]
        if lo <= hour < hi or busy:
            step = S.TICK_MINUTES
        else:
            step = ((lo - hour) % 24) * 60 - (m % 60)
        step = max(S.TICK_MINUTES, min(step, end - m))
        return step - step % S.TICK_MINUTES or S.TICK_MINUTES

    def tick(self, adv: dict) -> None:
        t0 = time.perf_counter()
        st = self.client.state()
        self.set_time(st)
        self.invalidate()
        events, isecs = self.ingest_pending(adv.get("new_chunks"))
        self.process_config_changes()
        t1 = time.perf_counter()
        tick_hour = ((self.minute - 1) // 60) % 24
        dialling = any(w.window[0] <= tick_hour < w.window[1] for w in self.wfs.values())
        self.gov.tick()
        self.health.tick(self.hb, dialling)
        self.inc.tick(self.hb, dialling)
        if self.minute % 60 == 0 and self.hour in S.LOOK_HOURS:
            for w in self.wfs.values():
                try:
                    self.exp.look(w, self.hb)
                except ApiError as e:
                    log(f"look {w.id} failed: {e}")
        self.housekeeping()
        rss = _rss_mb()
        with self.store.tx():
            self.store.put("sim_minute", self.minute)
            pend = self.store.q1("SELECT COUNT(*) FROM proposals WHERE status IN ('intent','pending','approved',"
                                 "'applying')")[0]
            self.store.x("INSERT OR REPLACE INTO loop_metrics(sim_minute, wall, events, ingest_secs, decide_secs,"
                         " credits, rss_mb, pending) VALUES(?,?,?,?,?,?,?,?)",
                         (self.minute, time.time(), events, isecs, time.perf_counter() - t1, self.credits, rss, pend))
        if self.minute % 1440 == 0:
            log(f"{st['ts']} day done: events={events} rss={rss:.0f}MB credits={self.credits} "
                f"tick={time.perf_counter() - t0:.2f}s")

    def housekeeping(self) -> None:
        day = self.hb // 24
        last = self.store.get("hk_day")
        if last == day:
            return
        self.ingestor.leads.evict_before(day - self.keep_days)
        with self.store.tx():
            self.store.x("DELETE FROM leads WHERE day < ?", (day - self.keep_days - 1,))
            if last is not None:
                for w in self.wfs.values():
                    k = kpis(self.store, w.id, (day - 1) * 24, day * 24)
                    cfg = self.live_config(w.id)["config"]
                    self.store.decide(w.id, "daily_summary", f"D{day - 1}: pickup {k['pickup_rate']} engagement "
                                      f"{k['engagement_rate']} flagged {k['halluc_rate']}",
                                      {"kpis": k, "config": cfg})
            self.store.put("hk_day", day)

    def run(self) -> None:
        self.setup()
        ticks = 0
        while True:
            st = self.client.state()
            self.set_time(st)
            if st["finished"]:
                break
            step = self.next_step(st)
            try:
                adv = self.client.advance(step)
            except ApiError as e:
                if e.code == "finished":
                    break
                raise
            self.tick(adv or {})
            ticks += 1
            if self.max_ticks and ticks >= self.max_ticks:
                log("max ticks reached; exiting (test mode)")
                return
        self.gov.tick()
        with self.store.tx():
            self.store.decide(None, "run_finished", f"scenario finished at {self.store.sim_ts}",
                              {"credits_remaining": self.credits, "ingested_events": self.ingestor.total_events})
        log(f"scenario finished. state: {self.store.path}")


def _rss_mb() -> float:
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except OSError:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
