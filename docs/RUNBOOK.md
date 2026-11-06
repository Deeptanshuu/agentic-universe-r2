# Controller runbook (for whoever is on call)

## Run
```bash
./run.sh http://127.0.0.1:8700                      # runs to scenario end; state in state/<scenario>.sqlite
CONTROLLER_STATE_DIR=/var/lib/ctl ./run.sh <url>    # put state elsewhere
scripts/run_scenario.sh scenarios/public_02_incidents.yaml mine02 base02 8702 reports/public_02.md  # sim+run+grade
```
Killed? Run the same command again. It takes a file lock (one controller per state file), reconciles every
proposal with the simulator and continues. If the simulator itself was restarted (a new world), the old state
file is archived as `*.old` automatically.

## Look
```bash
python -m controller status --db state/<scenario>.sqlite [--wf ks_cancel]   # the one-screen view
python -m controller why    --db ... --wf nw_renewal --at "D3 14:00"        # why is it configured like this?
python -m controller log    --db ... --wf nw_kyc --kind proposal --limit 50  # decision log (kinds below)
python -m controller health --db ...                                         # ingest rate, decide time, RSS, credits
sqlite3 state/<scenario>.sqlite                                              # everything is plain SQL
```
Decision kinds: `proposal`, `request_sent`, `applied`, `rejected`, `expired`, `stale`, `lapsed`, `promotion`,
`rollback`, `experiment_live`, `experiment_eval`, `look`, `experiment_paused`, `supervisor_edit`,
`incident_open`, `incident_resolved`, `validation`, `llm_suggest`, `did_probe`, `did_active`, `did_cooldown`,
`guardrail_breach`, `daily_summary`, `deferred`.

## Alerts
| alert | severity | what to do |
|---|---|---|
| `hallucination_spike` | page if ≥ 3× baseline | The loop is already validating a fix and will send an approval request. Make sure the client approver sees it. |
| `no_fix_candidate` / `no_valid_fix` | page | No prompt passed validation. Get the client to choose; the loop won't guess. |
| `prompt_fight` | page | The supervisor keeps re-introducing a failing prompt. Escalate to the account owner. |
| `last_did_degraded` | page | The workflow has no healthy caller ID left. Get new numbers from telephony. |
| `credits_low` | page | The 15% credit reserve has been reached; incident validation is blocked. |
| `caller_id_degraded` | ticket | The DID was rotated out automatically. Check its spam label with the carrier the next business day. |
| `guardrail_breach` | ticket | The guardrail KPI is below history by more than its margin; caller-ID expansion is frozen. Check `status`. |
| `proposal_invalid` | ticket | The simulator/API rejected our payload (4xx). That's a schema bug, so look at `log --kind invalid`. |

## Change behaviour
All tunables are in `controller/settings.py`, each with its reason. The usual ones: `Z_WIN` / `MIN_N` (how
much evidence a winner needs), `EXPLOIT_HOLDBACK` (decay-sensor size), `LOOK_HOURS`, `REQUEST_RESERVE`,
`SUPERVISOR_PREFS` (per-workflow soft limits, e.g. Kestrel's 2/day after 11:00), `PROBE_SHARE`,
`DID_COOLDOWN_H`. Run `python -m pytest -q tests` after changing statistics. `test_false_winner_rate_is_controlled`
re-measures the false-winner rate.

## Code map
`loop.py` (tick order) → `ingest.py` (chunks → aggregates) → `governance.py` (the only writer of configs) ←
`experiments.py` (welcome/voice), `health.py` (caller-ID pool), `incidents.py` (hallucination). `analysis.py`
holds the stratified read queries, `stats.py` the pure math, `store.py` the schema, `audit.py` the read-only CLI views.

## Reset a retired DID (after the carrier clears it)
```sql
UPDATE dids SET state='candidate', since_hb=0 WHERE wf='ks_cancel' AND did='D2';
```
It will be re-probed at the next look (and is retired again if it fails).
