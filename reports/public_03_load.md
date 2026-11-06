# public_03_load — load-test numbers

Machine: the same 2-vCPU / 8 GB Linux VM ran the simulator **and** the controller (the target box is 4 vCPU).
Command: `CONTROLLER_STATE_DIR=state/mine03 ./run.sh http://127.0.0.1:8703` (see `state/load03.log` when reproduced),
controller wrapped in `/usr/bin/time -v`. Grade: `reports/public_03_grade.md`.

| metric | value |
|---|---|
| events ingested (history 7 days + 5 sim days) | 9,737,111 in 268 chunks |
| ingest throughput (parse + aggregate + commit, single thread) | **39,200 events/s** average (history: 41,200/s) |
| history backfill (5.54M events) | 134 s |
| controller wall-clock per simulated day (includes waiting on the simulator to simulate it) | **47–53 s** (budget: 600 s) |
| of which ingest / decisions | 21–24 s / 3.9–5.9 s per day |
| decision lag per 15-min tick (events available → decisions done) | ingest avg 0.43 s (max 3.9 s) + decide avg 0.09 s (max 2.1 s) |
| whole scenario, controller process | 390 s wall, 220 s user CPU |
| peak RSS (controller) | **290 MB** (`time -v` max RSS) |
| state file | 134 MB SQLite |
| outcome vs no-op baseline | conclusion rate **+19.6%** (12 workflows), 62 changes: 36 beneficial, 16 neutral, 10 harmful (all caller-ID probes/rebalances), 0 credits |

Per simulated day (from `python -m controller health --db state/mine03/public_03_load.sqlite`):

```
day | events | ingest s | decide s | wall s | ev/s | peak RSS MB | credits | approvals pending
D0 | 840060 | 21.4 | 5.9 | 47 | 39322 | 255 | 400.0 | 12
D1 | 840036 | 24.3 | 5.5 | 53 | 34557 | 255 | 400.0 | 8
D2 | 840017 | 22.2 | 3.9 | 48 | 37835 | 255 | 400.0 | 2
D3 | 840026 | 22.4 | 4.4 | 48 | 37567 | 255 | 400.0 | 2
D4 | 840017 | 23.8 | 5.1 | 52 | 35304 | 255 | 400.0 | 2
```

**At 10×** (~8.4M events/day): parsing at ~39k events/s is ~215 s/day on one core, which still fits the 10-minute
budget. What breaks first is **memory and the single writer**: the in-RAM lead-feature index scales with active
leads (~0.8M here, ~8M at 10× ≈ 1.5–2 GB), and every chunk commits through one SQLite writer. Next step: shard by
tenant (no state crosses workflows), integer-encode lead features (or join on the producer side), and move the
store to Postgres with one schema per shard.
