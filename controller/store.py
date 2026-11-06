"""SQLite state store: the controller's only memory.

Everything needed to resume after a kill lives here: the ingestion watermark, the aggregates, the lead
features, every proposal (with its write-ahead status), the experiment/scope state and the audit log.
Python's stdlib sqlite3 in WAL mode; no new stateful infrastructure (see stakeholders/03).

Crash-safety rule used throughout: one chunk of events (aggregates + watermark) is ingested in ONE
transaction, and every outbound side effect is preceded by a committed "intent" row.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS chunks(
  chunk INTEGER PRIMARY KEY, kind TEXT, events INTEGER, last_event_id INTEGER,
  until_minute INTEGER, ingested_wall REAL, ingest_secs REAL);

CREATE TABLE IF NOT EXISTS workflows(wf TEXT PRIMARY KEY, tenant TEXT, spec TEXT);

-- per (workflow, hour bucket, config version, config cohort, segment, dimension, arm)
CREATE TABLE IF NOT EXISTS arm_stats(
  wf TEXT, hb INTEGER, ver INTEGER, cohort TEXT, seg TEXT, dim TEXT, arm TEXT,
  n INTEGER, conn INTEGER, eng INTEGER,
  PRIMARY KEY(wf, dim, seg, hb, ver, cohort, arm)) WITHOUT ROWID;

-- per (workflow, hour bucket, config cohort): workflow-level KPIs and unique leads dialled
CREATE TABLE IF NOT EXISTS kpi_stats(
  wf TEXT, hb INTEGER, cohort TEXT, n INTEGER, conn INTEGER, eng INTEGER, hal INTEGER, uniq INTEGER,
  PRIMARY KEY(wf, hb, cohort)) WITHOUT ROWID;

-- per (workflow, hour bucket, prompt): hallucination monitoring
CREATE TABLE IF NOT EXISTS prompt_stats(
  wf TEXT, hb INTEGER, prompt TEXT, n INTEGER, conn INTEGER, eng INTEGER, hal INTEGER,
  PRIMARY KEY(wf, prompt, hb)) WITHOUT ROWID;

-- per (workflow, hour bucket, caller id): caller-ID health
CREATE TABLE IF NOT EXISTS cid_stats(
  wf TEXT, hb INTEGER, cid TEXT, n INTEGER, conn INTEGER,
  PRIMARY KEY(wf, cid, hb)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS leads(lead_id TEXT PRIMARY KEY, wf TEXT, day INTEGER, f TEXT) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS leads_day ON leads(day);

CREATE TABLE IF NOT EXISTS campaigns(campaign_id TEXT PRIMARY KEY, wf TEXT, day INTEGER, leads INTEGER,
  ver_at_creation INTEGER);

CREATE TABLE IF NOT EXISTS config_versions(
  wf TEXT, version INTEGER, actor TEXT, changed_dims TEXT, proposal_id TEXT, approval_id TEXT,
  applied_at TEXT, hb INTEGER, config TEXT, PRIMARY KEY(wf, version));

CREATE TABLE IF NOT EXISTS proposals(
  proposal_id TEXT PRIMARY KEY, wf TEXT, channel TEXT, kind TEXT, status TEXT,
  approval_id TEXT, base_version INTEGER, applied_version INTEGER,
  body TEXT, plan TEXT, summary TEXT, changed_dims TEXT, reasons TEXT,
  created_sim TEXT, sent_sim TEXT, sent_hb INTEGER, decided_sim TEXT, applied_sim TEXT,
  apply_by TEXT, expires_at TEXT, superseded_by TEXT);
CREATE INDEX IF NOT EXISTS proposals_wf ON proposals(wf, status);

-- one row per (workflow, cohort, dimension): the experiment / exploitation state machine
CREATE TABLE IF NOT EXISTS scopes(
  wf TEXT, cohort TEXT, dim TEXT, mode TEXT, champion TEXT, incumbent TEXT,
  excluded TEXT, mode_since_hb INTEGER, explore_from_hb INTEGER, history TEXT,
  inflight TEXT, PRIMARY KEY(wf, cohort, dim));

-- shared cohort objects (Product memo: one definition used everywhere)
CREATE TABLE IF NOT EXISTS cohorts(wf TEXT, cohort_id TEXT, where_json TEXT, feature TEXT, value TEXT,
  status TEXT, created_sim TEXT, reason TEXT, PRIMARY KEY(wf, cohort_id));

-- caller-ID rotation pool state per DID: candidate | probing | active | flagged
CREATE TABLE IF NOT EXISTS dids(wf TEXT, did TEXT, state TEXT, since_hb INTEGER, detail TEXT,
  introduced_hb INTEGER, PRIMARY KEY(wf, did));

CREATE TABLE IF NOT EXISTS incidents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, wf TEXT, kind TEXT, key TEXT, status TEXT,
  opened_sim TEXT, opened_hb INTEGER, resolved_sim TEXT, detail TEXT);

CREATE TABLE IF NOT EXISTS validations(validation_run_id TEXT PRIMARY KEY, wf TEXT, prompt TEXT, n INTEGER,
  pass_rate REAL, sim_ts TEXT, incident_id INTEGER);

CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, sim_ts TEXT, hb INTEGER, wf TEXT, kind TEXT, summary TEXT,
  detail TEXT, proposal_id TEXT, approval_id TEXT, version INTEGER, wall REAL);
CREATE INDEX IF NOT EXISTS decisions_wf ON decisions(wf, hb);

CREATE TABLE IF NOT EXISTS loop_metrics(
  sim_minute INTEGER PRIMARY KEY, wall REAL, events INTEGER, ingest_secs REAL, decide_secs REAL,
  credits REAL, rss_mb REAL, pending INTEGER);

CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, sim_ts TEXT, wf TEXT, severity TEXT, kind TEXT, message TEXT,
  UNIQUE(wf, kind, message));
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self._in_tx = False
        self.sim_ts = ""
        self.hb = 0

    # ---------------------------------------------------------------- transactions
    @contextmanager
    def tx(self):
        if self._in_tx:  # nested: join the outer transaction
            yield self.db
            return
        self.db.execute("BEGIN IMMEDIATE")
        self._in_tx = True
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        finally:
            self._in_tx = False

    def q(self, sql: str, args=()):
        return self.db.execute(sql, args).fetchall()

    def q1(self, sql: str, args=()):
        return self.db.execute(sql, args).fetchone()

    def x(self, sql: str, args=()):
        return self.db.execute(sql, args)

    # ---------------------------------------------------------------- meta
    def get(self, k: str, default=None):
        r = self.q1("SELECT v FROM meta WHERE k=?", (k,))
        return json.loads(r[0]) if r else default

    def put(self, k: str, v) -> None:
        self.x("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
               (k, json.dumps(v)))

    # ---------------------------------------------------------------- audit
    def decide(self, wf: str | None, kind: str, summary: str, detail=None, proposal_id=None,
               approval_id=None, version=None) -> None:
        """Append one row to the decision log. This is the audit trail (constraint 6)."""
        self.x("INSERT INTO decisions(sim_ts, hb, wf, kind, summary, detail, proposal_id, approval_id, version,"
               " wall) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (self.sim_ts, self.hb, wf, kind, summary, json.dumps(detail, default=str) if detail else None,
                proposal_id, approval_id, version, time.time()))

    def alert(self, wf: str | None, severity: str, kind: str, message: str) -> None:
        self.x("INSERT OR IGNORE INTO alerts(sim_ts, wf, severity, kind, message) VALUES(?,?,?,?,?)",
               (self.sim_ts, wf, severity, kind, message))
