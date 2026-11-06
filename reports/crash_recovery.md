# Crash-recovery test — scenarios/public_02_incidents.yaml

Command: `.venv/bin/python scripts/crash_test.py --scenario scenarios/public_02_incidents.yaml --kills 10 --seed 21`

The controller was SIGKILLed (no shutdown hooks) at random wall-clock moments — the first one inside the history ingestion, the rest at random simulated times across the scenario — and restarted with the same `./run.sh` each time. Wall time 48s.

## Kills

| # | sim time at kill | proposals in flight at the kill (from our store) |
|---|---|---|
| 1 | 2027-01-04T00:00:00+05:30 | {} |
| 2 | 2027-01-06T08:15:00+05:30 | {'pending': 1} |
| 3 | 2027-01-06T13:30:00+05:30 | {} |
| 4 | 2027-01-09T10:15:00+05:30 | {} |
| 5 | 2027-01-09T10:15:00+05:30 | {} |
| 6 | 2027-01-09T13:00:00+05:30 | {'pending': 1} |
| 7 | 2027-01-10T09:15:00+05:30 | {} |
| 8 | 2027-01-10T18:30:00+05:30 | {'pending': 1} |
| 9 | 2027-01-11T15:30:00+05:30 | {} |
| 10 | 2027-01-12T11:00:00+05:30 | {'pending': 1} |

## Invariants

| check | result | detail |
|---|---|---|
| 1 finished | PASS | `"2027-01-14T00:00:00+05:30"` |
| 2 no double apply | PASS | `{"dup_approvals": [], "dup_proposals": [], "controller_versions": 23}` |
| 3 no lost approval | PASS | `{"sim_approvals": 15, "lost": []}` |
| 4 no dangling write-ahead rows | PASS | `[]` |
| 5 every applied version recorded | PASS | `{"missing": []}` |
| 6 events ingested exactly once | PASS | `{"ingested": 324361, "sim_last_event_id": 324361, "chunks": 491}` |
| 7 restarted after every kill | PASS | `{"starts_final": 10, "kills": 10, "starts_recorded_at_each_kill": [1, 2, 3, 4, 4, 5, 6, 7, 8, 9]}` |

Grade of the interrupted run: `reports/crash02_grade.md`.
