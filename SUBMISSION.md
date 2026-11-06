# Outcome-First Agent Loop — Submission

**Name:** Deeptanshu Lal · **Date:** 2026-11-06 · **Hours actually spent (be honest):** ~3.5 h wall-clock, built
by an AI coding agent (Claude Code) working autonomously from my brief; my own review time is not included ·
**Language / storage:** Python 3.11+ (stdlib + httpx; numpy only in tests) / one SQLite file (stdlib `sqlite3`, WAL)

> Run it: `./run.sh http://127.0.0.1:8700`. Inspect it: `python -m controller status|why|log|health --db state/<scenario>.sqlite`.
> Everything below is measured from this repo; the grade reports are in `reports/`.

---

## 0. TL;DR

- One Python process, one SQLite file. It ingests the event stream into hourly aggregates, runs factorial
  welcome×voice experiments with a stratified sequential test, promotes winners with a 15% holdback, detects decay
  and rolls back, keeps caller IDs (DIDs) in a health-checked rotation pool, and handles hallucination incidents
  (detect → validate → approve → verify). Every change goes through `/approvals` or policy-permitted auto-apply,
  with a write-ahead log so a kill at any point neither double-applies nor loses an approval.
- **public_01:** conclusion rate **+13.6%** vs baseline (18 changes applied: 11 beneficial, 4 neutral, 3 harmful). **public_02:** **+27.1%**,
  hallucinated calls 187 vs 406 in the baseline, flagged DID out of rotation the same morning, bad supervisor prompt reverted
  through approval in 165 min, the decaying W3 never promoted. **public_03:** conclusion rate **+19.6%** across 12 workflows at ~840k events/day; one simulated day takes 47–53 s of wall-clock (simulator included) at ~39k events/s ingest, peak RSS 290 MB.
- Deliberately not built: per-person call time (C4), bootstrapping from history (C3), a UI (C11 is a CLI
  screen), fair allocation across tenants (C15), proactive prompt experiments.

## 1. Assumptions

1. **Guardrails are absolute drops** in the rate (`pickup_rate_max_drop: 0.02` = 2 percentage points), judged
   against the workflow's own pre-D0 history, because the counterfactual is not observable from the API.
2. Hours in policies are IST and half-open (`approver_hours: [10, 19]` = 10:00–18:59).
3. `max_requests_per_24h` is a rolling 24h window over the times **we** sent requests. We keep one slot free for
   incidents when the cap is ≥ 2.
4. The simulated approver is the "named human approval" Compliance asks for. The API exposes `approval_id` and
   `decided_at` but no name, so we record those; the client maps IDs to people on their side.
5. Caller-ID rotation and call timing are *operational* (Compliance memo, point 1). We auto-apply them only where
   the workflow's policy lists the dimension (`nw_renewal`, `ks_cancel`); on `nw_kyc` (auto-apply disabled) they
   go through approval like everything else.
6. Welcome message, voice and prompt are customer-facing. **We never auto-apply them, even where a policy would
   allow it** (`ks_cancel` allows welcome/voice within `max_shift` 0.2): that's voluntary restraint, see §6.
7. A supervisor edit expresses intent. We stop changing that dimension for 24h and then keep the supervisor's
   choice as champion. Safety incidents are the exception, and even then the fix goes to approval.
8. Prompts are workflow-wide (no per-cohort prompt), so no concurrent control is possible and a before/after
   prompt test is confounded by everything else that changes. We change prompts **only** as incident response.
9. DIDs are per-workflow resources (`D1` on `nw_kyc` is not `D1` on `ks_cancel`).
10. Lead features are static for a lead, and `person_id` is stable across campaigns.
11. Welcome/voice act on engagement (they're heard after pickup), caller ID and hour act on pickup. We still
    measure pickup per welcome/voice arm, so a violation of this assumption would be caught by the guardrail check.
12. D0 is derived from `/sim/state` (`ts − sim_minute`), not hard-coded.
13. One simulator world per scenario name: if the simulator's minute or event id is behind our store, it's a new
    world and we archive the old state file.
14. Kestrel's supervisor preferences (≤ 2 requests/day, none before 11:00) bind **routine** requests on
    `ks_cancel`. Incidents may use the policy's own limit (4/24h, from 10:00).
15. The QA `hallucination_flag` is noisy in both directions. The relevant baseline is the workflow's own historical
    flag rate, not zero.

## 2. Scope decisions

| # | Capability | Built / Partial / Cut | Why | What it would take to finish |
|---|---|---|---|---|
| C1 | Event ingestion | **Built** | Everything rests on it; must be idempotent to be crash-safe | Partition by tenant for >1 process (§8) |
| C2 | Lead/feature joins, shared cohort | **Built** | Product's ask, needed for heterogeneity | Multi-feature cohorts with hierarchical shrinkage |
| C3 | Bootstrapping from history | **Cut** (partial use) | History has only the default arm (W1/V1), so it can't rank variants. We *do* use it for baselines (flag rate, DID pickup, guardrails) | Per-segment hour-of-day priors for C4 |
| C4 | Call-time optimisation | **Cut** | Smallest expected lift per hour of work here; per-person needs 200k-entry maps and careful exploration | Hour-weight bandit per cohort on `schedule` (auto-applicable on 2/3 workflows) |
| C5 | Caller-ID health | **Built** | A flagged DID cuts pickup 40% within hours; public_02 has one; DIDs fatigue under concentrated volume | Carrier spam-label API as a second signal |
| C6 | Variant experimentation | **Built** | Where the engagement lift is | Interaction (W×V) cells, Thompson sampling within approved ranges |
| C7 | Promotion, decay, rollback | **Built** | Winners decay (W3 on renewal) | Change-point model instead of fixed windows |
| C8 | Hallucination incidents | **Built** | Compliance's biggest risk; public_02 has one | LLM-suggested fixes engagement-tested before full rollout |
| C9 | Governance client | **Built** | Constraint #1; approver fatigue and stale bases are where naive loops break | Smarter batching across days (queue planning) |
| C10 | Impact, affected leads, dry run | **Partial** | Honest replay from observed arm rates; impact score is simple | Off-policy estimators with variance, calibrated impact |
| C11 | Supervisor view | **Partial** | `python -m controller status`: one screen per workflow (tested / winning / waiting on you / changed today / incidents) | Read-only web page over the same queries |
| C12 | Decision/audit store | **Built** | Constraint #6 and the live review | Per-call variant log retention for IRDAI (§3.02) |
| C13 | Crash recovery, demonstrated | **Built** | Constraint #8, SRE non-negotiable | — |
| C14 | Loop observability | **Partial** | Per-tick metrics + alerts table + `health` command | Prometheus exporter, paging integration |
| C15 | Fair allocation across tenants | **Cut** | Credits barely used (6 of 200/400); approver budgets are per workflow already | Per-tenant credit quotas + round-robin incident queue |

**Principle.** First the things that make the loop *safe to run unattended*: ingestion, governance, crash
safety, audit. Then the two levers that move outcomes in this world: variants on engagement workflows and caller
IDs on pickup. Then the incident paths a regulated client would call about: a hallucinating prompt and a burnt
number. I cut what either needs data the API doesn't give us (C3 for variants), or buys less per hour than the
above (C4, C15, UI).

## 3. Stakeholders

### 01 — Sales (Nikhil)
- **Accepted:** a per-client lift number. `reports/` gives conclusion rate, pickup and engagement per workflow
  versus a no-op baseline.
- **Declined:** a single blended score (Product forbids blending; so does the platform contract); "live for
  everyone by evening" (a variant needs ≥ 300 connected calls per arm and z ≥ 2.6 before we call it, about 1.5–3 days
  at Northwind volumes); pushing the persuasive prompt everywhere.
- **Wrong claims:** *"Kestrel has auto-apply for everything"*: `ks_cancel` allows auto-apply only for
  schedule, caller ID, welcome and voice, at most a 0.2 shift per change, and never for prompts. *"Northwind is
  fine with auto-apply on KYC"*: `nw_kyc` has `auto_apply.enabled: false`, and Northwind Compliance requires a
  named human approval for anything customers hear. *Persuasive prompt*: there is no cross-client promotion (Product),
  and a prompt needs validation ≥ 0.97 **on that workflow**. "Freer objection handling" is exactly what raises
  hallucination risk.
- **Reply:** "Nikhil, you'll have a defensible per-client number: on our test worlds the loop raised leads
  concluded by +13.6% and +27.1%, and every change has a written reason a client can read. Two corrections before the summit.
  Kestrel's policy auto-applies only small shifts and never scripts; Northwind KYC has auto-apply switched off,
  and their Compliance head requires human approval for anything customers hear. So the story is 'improves itself
  overnight, with the client's sign-off', not 'without asking'. That's the version a BFSI audience buys. No
  blended score: pickup and engagement are separate by contract. The persuasive prompt has to pass validation on
  each workflow first."

### 02 — Client Compliance (Meera, Northwind)
- **Accepted:** every customer-facing change goes through approval (we don't auto-apply welcome, voice or prompt
  anywhere). Prompt changes carry `evidence.validation_run_id`, and we only accept a candidate if the Wilson lower
  bound of its pass rate clears your 0.97 threshold. Requests are only sent inside your approver hours (the
  last hour is kept free); only incident fixes may queue outside them. "Slow and right" is built in: harm stops
  (arms significantly worse are pulled), decay rollback, and a guardrail check on every promotion.
- **Wrong claim:** *"caller-ID rotation is covered by the policy we signed"*. True for **renewal** only.
  `nw_kyc`'s policy has auto-apply disabled, so on KYC we send caller-ID changes for approval too. Also, the
  approval record we get carries an approval ID and timestamp, not a person's name.
- **IRDAI question ("why did this customer hear X on day D"):** `python -m controller why --wf nw_renewal --at "D3 14:00"`
  answers from our store: config version, who approved it and when, the evidence, and what happened next. The
  exact variant on a call is sampled from that allocation and recorded on the call event. We keep hourly
  aggregates, not per-call rows, so the per-call log of record is the dialler's CDR (gap noted in §13).
- **Reply (≤120 words):** "Meera, agreed on all five points, and they're enforced in code: no change to what a
  customer hears without your approver's sign-off, validation evidence attached to every script change, requests
  only 10:00–18:00 (KYC 10:00–17:00). One correction: your KYC policy has auto-apply switched off entirely, so even
  caller-ID rotation on KYC comes to you for approval; on renewal it's auto-applied and listed in the weekly
  summary. For auditors we can show, for any workflow and time, the live configuration, who approved it, the
  evidence and the outcome afterwards. Your approval IDs map to names on your side."

### 03 — Platform & SRE (Kabir)
- **Accepted:** kill/restart test (`scripts/crash_test.py`, `reports/crash_recovery.md`), idempotency everywhere
  (deterministic proposal IDs, write-ahead rows, chunk-atomic ingestion), and no new stateful infrastructure (one
  SQLite file; Postgres is the natural production home, same schema). Loop health without logs:
  `python -m controller health` and the `loop_metrics` table. Page-vs-ticket split in §10.
- **Pushed back:** nothing outright. I'll be honest about the 10× limits (§8).
- **Reply:** "Kabir, kill/restart evidence: `reports/crash_recovery.md` (10 SIGKILLs at random
  points, including mid-ingest; every invariant passes: no double apply, no lost approval, every event counted
  once). State is a single SQLite file, no new service; it ports to our Postgres unchanged. Load: one simulated
  day of public_03 takes 47–53 s of controller time at ~39k events/s, peak RSS 290 MB. At 10×
  the first thing to break is the single ingest process; the fix is partitioning by tenant, which the state
  model already allows. Pages only for what can't wait: a hallucinating prompt with no valid fix, the last usable
  caller ID degraded, credits at reserve, the loop stalled."

### 04 — Client Ops Supervisor (Anita, Kestrel)
- **Accepted:** we never undo your edits silently. A supervisor change pauses our changes on that dimension for
  24h, and afterwards your choice stays as champion. Routine requests are capped at **two a day and never before
  11:00** (your policy allows 4 from 10:00). Changes are batched: one request carries welcome + voice. There is one
  screen (`controller status --wf ks_cancel`). And no "verbatim script" fixes: a fix prompt is chosen by
  production engagement evidence first, not by validation pass rate alone, which would always pick the strict
  script.
- **Pushed back:** caller-ID rotation on `ks_cancel` is auto-applied (operational, and allowed by your policy).
  It shows on your screen under "changed today" rather than as a request. A safety incident can exceed your 2/day.
- **Not exactly wrong, but worth saying:** in public_02 your P2 edit roughly quadrupled flagged calls. We
  asked for approval to revert it; we didn't revert it ourselves.
- **Reply:** "Anita, done as asked: at most two requests a day from us, none before 11:00, several changes
  bundled into one request, and one screen showing what's tested, what's winning, what needs you and what
  changed today. If you edit something, we leave that setting alone for a day and then keep your choice as the
  baseline. The one exception is safety: if a prompt starts producing misleading statements, we'll send you a
  request to switch back, with the evidence, even if it's a third request that day. Caller-ID rotation happens
  automatically (your policy allows it) and shows up on the screen."

### 05 — Product (Tara)
- **Accepted:** separate KPIs everywhere (primary vs guardrail per workflow). **One cohort object**: a row in
  `cohorts` whose ID is the config cohort ID, the analysis segment and the status-view label. Minimum sizes with
  reasoning (§5). Every proposal carries impact, diff, affected leads and a dry run. Expired = rejected, then
  re-queued at the next look with fresh data, never resubmitted as-is. "Only what happened counts": scope state
  changes only when a proposal is **applied**, and statistics use the per-call variant actually dialled. Winners
  stay inside their workflow.
- **Pushed back:** three P0s. Caller-ID health is built; the supervisor view is a CLI screen; per-person call time
  is cut (it would have displaced experiment rigour and crash safety).
- **Wrong assumption:** that the impact score can rank a queue "as is". Exploration requests have no expected
  lift yet, so we give them a fixed 0.2 (option value), and the dry run says so. That keeps promotions and incidents
  above experiments.
- **Reply:** "Tara, definitions held: separate KPIs, one cohort object end to end, expiry treated as rejection,
  learning only from arms that actually ran. Minimum sample is 300 connected calls per arm for engagement (600
  dials for pickup), z ≥ 2.6 at fixed twice-daily looks: about 6% false winners per experiment by simulation,
  and a 5-point lift gets called in roughly 4–5 days at renewal volume (well under a day at load volume). Of your three P0s I built caller-ID
  health, shipped the supervisor view as a one-screen CLI, and cut per-person call time: it would have cost the
  rigour that makes the other numbers trustworthy. Impact scores rank incidents and proven wins above
  exploration."

### 06 — CTO (Aryamann)
- **Accepted:** explainability first (every change has a stored rationale, evidence and outcome); harm-avoidance
  rules (harm stop, decay rollback, guardrail); minimal compute (credits only after a population-level incident
  signal, production evidence before LLM calls: 6 credits used); design for many workflows (per-workflow
  state, aggregates instead of rows). The choices and the people I said no to are listed above.
- **Reply:** "Aryamann, what I chose, where it lives: ingestion, governance, crash safety and audit first, then
  experiments, caller-ID rotation and hallucination response. I cut per-person timing, bootstrapping, the UI and
  cross-tenant fairness, and drafted the no's to Sales and Product. Three things the memos had wrong are in the
  doc (Kestrel and KYC auto-apply, KYC caller rotation). The thing I'd want your eyes on: our own data says DIDs
  burn out under concentrated volume, so caller IDs are a rotation pool, not a 'winner'. That one finding was
  worth more pickup than any variant test."

## 4. Architecture

```
                          ┌────────────────────────── controller (one process) ───────────────────────────┐
  simulator (HTTP)        │                                                                              │
  ─────────────────       │  loop.py  advance 15 min (night: skip to next active hour)                     │
  POST /sim/advance ◀─────┤     │                                                                        │
  GET  /events/chunks ───▶│  ingest.py   stream NDJSON ─▶ hourly aggregates ─┐   one SQLite transaction    │
                          │              lead features (RAM: active leads)   ├─▶ per chunk: aggregates +    │
                          │              config_changed / approval_decided ──┘   control events + watermark │
                          │     │                                                                        │
  GET/POST /approvals ◀──▶│  governance.py  write-ahead proposals, poll, apply, reconcile, budgets ◀──┐     │
  POST /…/apply      ◀────┤     ▲                                                                   │     │
  PUT  /…/config     ◀────┤     │ desired changes                                                   │     │
                          │  experiments.py  explore → exploit(holdback) → decay → rollback  (looks)  │     │
                          │  health.py       caller-ID pool: probe/active/flagged + guardrail (tick)  │     │
  /validation, /llm  ◀────┤  incidents.py    hallucination: detect → validate → propose → verify ────┘     │
                          │     │                                                                        │
                          │  store.py  state/<scenario>.sqlite  (WAL)                                     │
                          │     tables: chunks · arm_stats · kpi_stats · prompt_stats · cid_stats · leads │
                          │             proposals · scopes · cohorts · dids · incidents · validations     │
                          │             config_versions · decisions (audit) · alerts · loop_metrics       │
                          └──────────────────────────────────────────────────────────────────────────────┘
       audit.py (read-only): status · why · log · health
```

**On restart** (`./run.sh` again): take the file lock; check the simulator is the same world (scenario, minute,
event id); reload active leads; **reconcile** every proposal in `intent`/`pending`/`approved`/`applying` by
`proposal_id`/`approval_id` against `GET /approvals` and `GET /config/history`; ingest chunks past the watermark;
continue at the simulator's current minute. Nothing in memory is authoritative.

## 5. The learning loop

**Cohorts.** One object, `cohorts(wf, cohort_id, where, feature, value)`. The analysis segment `age_band=60+`,
the config cohort `age_60plus` and the status label are the same thing. Cohorts are created only when the data
demands one: when a workflow-level decision is made, each single-feature segment carrying ≥ 10% of traffic is
tested for an arm that beats the chosen one at **z ≥ 3.2** (Bonferroni over ~14 segments × 3 arms), with
≥ 150 connected calls per arm inside the segment. At most 4 cohorts per workflow, **all on one feature**, so they
never overlap (first-match semantics would otherwise silently reassign leads). Each cohort gets its own
exploit scope. Why so strict: a cohort halves the data for everything inside it. Fragmenting a ~1,000-connected-
calls/day workflow into many cohorts would leave every one underpowered.

**Experiment design.** Factorial: the dialler samples welcome and voice independently, so one exploration config
tests both at once. The incumbent keeps 40% (bounded downside); challengers share the rest equally
(W: 40/30/30, V: 40/20/20/20). Only levers that move the *primary* KPI are tested: welcome × voice on engagement
workflows. Caller ID on pickup workflows is handled by the rotation pool (below). Comparisons are **stratified
by (day, config version, cohort)** (Cochran–Mantel–Haenszel risk difference) so allocation changes between days
can't create Simpson's-paradox winners (`tests/test_stats.py::test_stratified_diff_avoids_simpsons_paradox`).
Interactions: marginal effects are estimated under random independent assignment, which is unbiased for the
main effects. We don't model W×V interactions (§13).

**Deciding a winner.** Fixed looks at 11:00 and 15:00. At a look, a challenger wins if it has ≥ 300 connected
calls (≥ 600 dials for pickup), the incumbent too, **z ≥ 2.6** one-sided, the guardrail holds (pickup drop
< 2pp and not significantly negative) and **it is still ahead over the last 24h** (effects decay; this rule
kept public_02's already-decaying W3 from being promoted). Ties are broken by the highest lower confidence
bound, then the arm name, so the rule is fully deterministic. Final look (≥ 1,300 connected calls per arm ≈ 5pp
MDE at 80% power, or 96h): Holm threshold z ≥ z(0.05/m). Failing that, the leader is taken only if
P(better) ≥ 0.8 (z ≥ 0.84), otherwise the incumbent stays. **False-winner rate:** Monte-Carlo of exactly this
schedule under A/A gives **6.3% per dimension** (`test_false_winner_rate_is_controlled`); the same looks at
z ≥ 1.96 would be far worse. The lean rule switches to an *equal* arm in ~36% of null runs. That costs nothing
under the null, and under alternatives it can only pick an arm within the MDE. Arms significantly **worse**
than the incumbent (z ≤ −2.6 over the run, or over the last 48h) are stopped early (harm stop).

**Decay and rollback.** After promotion, 15% stays on the other arms as a concurrent control. Checked **every
approver hour** (rollbacks don't wait for a look): (a) the champion is significantly worse than the pooled
holdback over the last 24h (z ≤ −2.33), or (b) the champion's own rate fell ≥ 5pp versus the window that
promoted it (z ≤ −3 on its own large sample) *and* the fall isn't explained by drift on the holdback (combined
variance, z ≤ −2). (b) is disabled for `next_campaign_only` workflows, where campaign-age mix shifts after
every change. Rollback = move to the best holdback arm (rates since promotion, shrunk to the pooled mean so a small lucky arm
can't win), decayed arm excluded, holdback kept. We don't reopen full exploration, which would put 60% of traffic
on arms we've already measured. Speed: W3 on
renewal (public_01, onset D3) was caught at D5 14:00 and moved off by 15:30 (to W2); in public_02 it
never got promoted at all.

**Call time and caller ID.** History has no variant variation, so we use it only for baselines. Caller IDs turned
out to be the biggest lever, and **not** a "pick the winner" problem. Per-DID daily pickup from our own store
(public_02, before the pool policy existed):

| workflow / DID | volume regime | daily pickup |
|---|---|---|
| ks_cancel D3 after taking 100% (4,800/day) | D4→D9 | 0.269 → 0.240 → 0.216 → 0.197 → 0.196 → 0.185 |
| nw_kyc D1 at 100% for 14+ days (2,000/day) | history | ≈ 0.24 |
| nw_kyc D1 after load cut to ~400/day | D3→D5 | 0.288 → 0.285 → 0.303 (recovering) |
| nw_kyc D2/D3 fresh, low volume | D2→D6 | 0.31–0.39 |

Concentration burns a DID and rest restores it: the carrier spam-labelling pattern. So `health.py` runs a
**rotation pool**. Dials are spread **equally** over active DIDs. Unknown DIDs enter at a 10% probe share (20%
where every change costs an approval) and join after 400 dials if they're not worse than the *proven* DIDs. A DID
is flagged when its pickup is ≥ 20% below the pool on concurrent traffic at z ≤ −4 (probes fail faster:
z ≤ −2.5, −15%). Comparisons use the version-stratified arm data, so a probe dialling only fresh campaigns can't
look good just by being new. A flagged DID rests 72h and gets **one** re-probe (fatigue recovers, spam labels
don't). We never flag the last usable DID (page instead). Per-person timing (C4) is cut.

**Guardrail KPI.** Every promotion checks the guardrail (`_guard_ok`): a point drop beyond the margin, or a
significant drop beyond half of it, blocks the promotion. Caller-ID changes can buy pickup by reaching people
who don't stay on the call, so each DID **we introduced** is also checked on engagement against the rest of the
pool (> margin and z ≤ −2.5 → out). A workflow-level monitor compares the guardrail KPI over 72h with history
and freezes pool expansion on a breach.

## 6. Governance

```
 intent ─POST ok─▶ pending ─approved─▶ approved ─▶ applying ─200─▶ applied ──▶ scope/pool/incident hooks
   │                  ├─rejected─▶ rejected  ─┐            ├─409 stale─▶ stale   ─┐
   │                  └─expired──▶ expired   ─┼─▶ owning module re-plans on the    │
   ├─409 duplicate─▶ reconcile by proposal_id │   CURRENT config, new proposal_id ◀┘
   ├─409 stale────▶ stale_on_submit ──────────┘   (never the same body again)
   └─connection lost ─▶ stays intent ─▶ reconcile next tick (GET /approvals, /config/history)
 auto channel:  intent ─PUT─▶ applied | 403 auto_denied
```

- **Batching and timing.** One routine experiment request per workflow at a time, carrying every wanted change
  (welcome + voice + new cohorts). Sent only at the 11:00/15:00 looks, inside approver hours (not in the last
  hour), within `cap − 1` per rolling 24h. Kestrel: ≤ 2/day, ≥ 11:00. Incidents and decay rollbacks are urgent:
  any approver hour, may use the reserved slot. Approved changes are applied on the next 15-minute tick (apply
  window is 2h), and the loop never skips ahead while anything is approved but unapplied.
- **Auto-apply.** Used for caller-ID rotation where the policy allows `caller_id`, stepping within `max_shift`
  (`ks_cancel`: 0.2 per step, one step per tick). **Not used** for welcome/voice/prompt even where allowed. Those
  are what customers hear; Compliance and Kestrel's supervisor both asked to be asked, and approvals on Kestrel
  take 20–120 minutes, so restraint costs little. Also not used for routine caller changes within 24h of a
  supervisor's caller-ID edit.
- **Impact, affected leads, dry run.** Dry run = replay of the last 72h of observed per-arm outcomes in the
  affected cohorts, arm rates shrunk to the cohort mean (50-call prior); arms that never ran contribute the
  mean (no extrapolation). It reports expected primary and guardrail rates under the live and proposed configs.
  Affected leads = unique leads dialled in the last 24h × the traffic share of the affected cohorts (next-campaign
  size for `next_campaign_only`). Impact = expected primary lift / 5pp, clipped to [0.05, 1]; exploration 0.2;
  decay rollback ≥ 0.6; caller-ID removal 0.7; hallucination fix 0.9. Honest caveat: the replay can't see
  interactions or decay that hasn't happened yet.
- **Supervisor edits mid-experiment.** Detected from `config_changed{actor: supervisor}` inside the ingest
  transaction. The touched scopes go to `paused` (24h), then resume as `exploit` with the supervisor's choice as
  champion. In-flight proposals become stale, and if approved they fail with 409 and are re-planned on the new
  base (`tests/test_governance.py::test_supervisor_edit_makes_apply_stale_and_we_never_force_it`).

## 7. Incidents

| incident | signal | threshold | expected false alarms |
|---|---|---|---|
| hallucination | flagged rate of the live prompt, trailing 8h (since the prompt went live) | ≥ 2× workflow baseline, ≥ 6 flags, ≥ 100 connected, z ≥ 4 (12h after a supervisor prompt edit: z ≥ 3.5, ≥ 5 flags, ≥ 60 connected) | P(z≥4) ≈ 3e-5 per check → < 1 per workflow-year at 15-min checks; watch mode ≈ 0.2% per edit |
| caller ID degraded | DID vs pool pickup, 6h, version-stratified | ≥ 20% below, z ≤ −4 | ~3e-5 per DID per check |
| DID probe failed | probe vs proven DIDs since probe start | ≥ 15% below, z ≤ −2.5 | ~0.6% per probe check, deliberately loose: dropping an unproven DID is cheap |

**Picking a fix.** Production-proven prompts on this workflow come first (≥ 500 connected calls, flag rate ≤ 1.5×
baseline), ranked by observed engagement. That's usually "go back to what worked" and costs **0 LLM credits**.
Only if none exists do we call `/llm/suggest-fixes` (3 credits). Each candidate gets one validation run (n=600,
6 credits), and the first one whose Wilson lower bound clears `min_validation_pass_rate` is proposed. The LLM
proposes; the counts decide. **Verify:** after apply, ≥ 150 connected calls on the new prompt with a flag rate
not significantly above baseline (z < 2) closes the incident; otherwise the next candidate is tried. Max 2
prompt fixes/workflow/day, then a page (a supervisor fighting the loop is a human problem). Credits never go below
15% of the starting budget.

**public_02 timeline (ks_cancel):**
| sim time | what happened (from `python -m controller log --wf ks_cancel`) |
|---|---|
| D2 10:00 | *(grader)* DID D2 spam-flagged |
| D2 11:45 | `incident_open` caller ID D2: pickup 0.174 vs pool 0.290 (−40%, z=−4.5) → auto-applied rotation step (max_shift 0.2) |
| D2 12:00 | second step; D2 at 0% → `incident_resolved`. Flagged-DID share: 8% of that day's calls, 0 afterwards |
| D3 11:30 | *(grader)* supervisor ships prompt P2; we see `config_changed{actor: supervisor}` at the 11:45 tick and enter watch mode |
| D3 12:30 | `incident_open`: 7/126 connected calls flagged (5.6% vs 1.5% baseline, z=3.8 ≥ 3.5 watch threshold) |
| D3 12:30 | same tick: candidate P1 from production evidence (0 LLM credits), validation n=600 → 0.987, Wilson LB 0.976 ≥ 0.97 → approval request with `validation_run_id` |
| D3 13:50 | approver approves (latency 80 min) |
| D3 14:15 | applied (next tick) — **MTTR 165 min** per grader, of which 80 min approver latency |
| D3 15:45 | verification: 1/153 flagged on P1 (0.7%) → `incident_resolved` |
| D5 11:00 | D2's 72h rest over → one re-probe at 10% (graded harmful, −0.061) → fails → retired for the run |

## 8. Scale

Measured on `public_03_load` (12 workflows, 16k leads/day each), on this 2-vCPU / 8 GB box with the simulator
running on the same machine:

| metric | value |
|---|---|
| events ingested (7 history days + 5 sim days) | 9.74M in 268 chunks |
| ingest throughput (parse + aggregate + commit, one thread) | **39.2k events/s** average (history backfill 41.2k/s, 5.5M events in 134 s) |
| wall-clock per simulated day (includes the simulator simulating it) | **47–53 s** (SRE budget: 600 s) |
| of which ingest / decisions | 21–24 s / 3.9–5.9 s per day |
| decision lag per 15-min tick | ingest avg 0.43 s (max 3.9 s) + decide avg 0.09 s (max 2.1 s) |
| peak RSS (controller) | **290 MB** · state file 134 MB |
| outcome (`reports/public_03_grade.md`) | conclusion rate **+19.6%**; 62 changes: 36 beneficial, 16 neutral, 10 harmful (caller-ID probes/rebalances) |

Details and per-day breakdown: `reports/public_03_load.md`.

- **Where the time goes.** JSON parsing and dict aggregation in one Python thread (~11 dict updates per call).
  Decisions are per-workflow SQL over hourly aggregates (~hundreds of rows per workflow-day), not over calls.
- **At 10× (≈ 6.5M dials/day, ~8.4M events/day)** parsing still fits (~215 s/day on one core). What breaks
  first is **memory and the single writer**: the in-RAM lead-feature index grows with active leads (~0.8M
  here, ~8M at 10× ≈ 1.5–2 GB), and every chunk commits through one SQLite writer. Beyond 10× the single process
  is the limit.
- **Partitioning.** All state is keyed by workflow and nothing crosses workflows (approver budgets, DIDs and
  cohorts are per workflow). Shard by tenant: N controller processes, each owning a tenant set, its own
  state file (or schema in Postgres) and its own event stream partition. The only global things are the credit
  budget (a shared counter with per-tenant quotas, i.e. C15) and the clock.

## 9. Reliability

- **Crash test** (`scripts/crash_test.py`, results in `reports/crash_recovery.md`): 10 SIGKILLs on public_02: the first inside history ingestion, then at random simulated
  times across the 10 days (three with approval requests in flight, one landing during interpreter start-up),
  each followed by a plain `./run.sh` restart. **All 7 invariants pass**: the scenario finished; no approval
  applied twice and no duplicate proposal_id in the simulator's config history (27 controller versions); all 16
  approval requests the simulator knows are in our store with matching IDs; no dangling `intent`/`applying`
  rows; every controller version recorded as applied; 324,367 events ingested exactly once (= the simulator's
  last event id); restart count consistent. The interrupted run's grade is **identical** to the uninterrupted
  run (+27.1%, the same 27 changes), which is the strongest evidence that nothing was lost or redone.
- **What we lose on a kill:** nothing durable. At most one chunk's aggregation is redone, and an in-flight HTTP
  call is reconciled. A kill longer than an approval's 2h apply window would let it lapse; it then gets
  re-planned (not covered by the short-kill test).
- **Idempotency keys:** chunk number (PK) for ingestion; deterministic `proposal_id` = `<wf>.<kind>.<seq>` (the
  sequence is committed with the intent row); `approval_id` for apply. Control events are applied inside the
  chunk transaction, so they're applied exactly once.
- **Simulator errors:** transport errors and 5xx on idempotent calls are retried with backoff. `/sim/advance` is
  never blindly retried (we re-read the minute first). 409 duplicate → reconcile; 409 stale → re-plan; 410 →
  lapsed → re-plan; 403 auto-apply → logged (incidents fall back to approval); 402 credits → page; other 4xx →
  `invalid` + ticket. A failed chunk download discards the scratch accumulator and re-reads the whole chunk.

## 10. Audit & observability

Real output from `python -m controller why --db state/mine02/public_02_incidents.sqlite --wf ks_cancel --at "D3 15:00"`:

```
# Why is ks_cancel configured the way it is at D3 15:00 (2027-01-07T15:00:00+05:30)?

Active config: v9, set at 2027-01-07T14:15:00+05:30 by **controller_approved**, changed ['prompt_version']

## The change: proposal `ks_cancel.incident.00015` (incident, via approval)
- summary: ks_cancel: switch prompt P2 -> P1 — hallucination flags 5.6% vs 1.5% baseline
- requested 2027-01-07T12:30:00+05:30; approval `apr_00007` approved at 2027-01-07T13:50:00+05:30 by the ks_cancel approver (simulated client approver; the API does not expose a name); applied 2027-01-07T14:15:00+05:30
- diff: {"prompt_version": {"from": "P2", "to": "P1"}}
- impact_score 0.9, affected_leads 2348
- rationale: Population signal: 7/126 connected calls flagged on P2 (z=3.81). Fix chosen from production evidence: P1 validated at 0.987 on n=600 (Wilson LB 0.9765). This reverts a supervisor edit; it is sent for explicit approval, never applied silently.
- dry run: {"method": "production flag/engagement rates per prompt on this workflow + validation run", "validation": {"prompt": "P1", "validation_run_id": "val_00001", "pass_rate": 0.9867, "n": 600, "wilson_lb": 0.9765, "accepted": true}}
- evidence: {"validation_run_id": "val_00001", "incident_id": 3}

## Decisions that led here (48h before)
- 01-06T11:45 [incident_open] caller ID D2 degraded: pickup 0.174 vs 0.290 (-40%, z=-4.5) — concurrent pool DIDs D1,D3
- 01-06T11:45 [proposal] [auto] ks_cancel: rotate out degraded caller ID D2
- 01-06T11:45 [applied] v6 applied via auto
- 01-06T12:00 [proposal] [auto] ks_cancel: rotate out degraded caller ID D2
- 01-06T12:00 [applied] v7 applied via auto
- 01-06T12:00 [incident_resolved] caller-ID incident 2: D2 has no traffic in the live config
- 01-07T11:45 [supervisor_edit] supervisor changed ['prompt_version'] -> v8; we adopt it as the new base and do not revert it silently
- 01-07T12:30 [incident_open] hallucination spike on prompt P2: 7/126 = 0.056 flagged vs baseline 0.015 (z=3.8)
- 01-07T12:30 [incident_candidates] candidates ['P1'] from production evidence
- 01-07T12:30 [validation] P1: pass 0.987 (n=600, Wilson LB 0.976 vs required 0.97) -> ACCEPT
- 01-07T12:30 [proposal] [approval] ks_cancel: switch prompt P2 -> P1 — hallucination flags 5.6% vs 1.5% baseline
- 01-07T12:30 [request_sent] approval apr_00007 pending
- 01-07T14:15 [applied] v9 applied via approval

## What happened next
- 24h before: pickup 0.2569 engagement 0.4899 flag rate 0.0316 (dials 4800)
- 24h after:  pickup 0.2592 engagement 0.4912 flag rate 0.0105 (dials 4800)
- 01-07T15:45 [incident_resolved] prompt P1: 1/153 flagged (0.007) — back at baseline 0.015; incident closed
- 01-08T15:00 [proposal] [approval] ks_cancel: use welcome_message W1
- 01-08T15:00 [request_sent] approval apr_00009 pending
```

**Pages** (someone must act now): hallucination spike ≥ 3× baseline with no valid fix or no fix candidate;
prompt fight (> 2 fixes/24h); last usable caller ID degraded; credits at reserve; loop stalled (no tick for
> 15 wall-clock minutes; from `loop_metrics`). **Tickets** (next business day): a DID rotated out (check its
spam label with the carrier), guardrail breach with expansion frozen, invalid proposal (schema bug), a
hallucination spike that the loop is already fixing. **Not alerts:** experiment decisions, rejections, expiries,
stale re-plans, supervisor edits. These are normal operation and live in `status`/`log`.

## 11. Results

### public_01 (dev)
| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.388 | +13.5% | 0.258 | +7.6% | 0.521 | +4.2% | 28% | 54 (52) |
| nw_kyc | 0.450 | +19.5% | 0.296 | +22.2% | 0.588 | -4.0% | 0% | 39 (21) |
| nw_renewal | 0.464 | +10.4% | 0.278 | +6.2% | 0.569 | +3.9% | 32% | 40 (39) |
| ALL | 0.426 | +13.6% | 0.272 | +9.9% | 0.552 | +2.5% | 22% | 133 (112) |

18 applied — harmful: 3, neutral: 4, beneficial: 11

Harmful:
- nw_renewal v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.025 → **harmful**
- nw_renewal v7 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.041 → **harmful**
- nw_kyc v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.027 → **harmful**

### public_02 (incidents)
| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.363 | +48.7% | 0.250 | +36.9% | 0.495 | +4.9% | 39% | 92 (310) |
| nw_kyc | 0.487 | +26.7% | 0.309 | +24.7% | 0.594 | -0.9% | 0% | 44 (38) |
| nw_renewal | 0.475 | +10.5% | 0.285 | +8.5% | 0.552 | +0.8% | 14% | 51 (58) |
| ALL | 0.427 | +27.1% | 0.274 | +22.7% | 0.537 | +1.3% | 27% | 187 (406) |

27 applied — beneficial: 14, neutral: 10, harmful: 3

Harmful:
- ks_cancel v10 (controller_approved) dims=['welcome_message'] engageΔ=-0.011 didΔ=+0.000 → **harmful**
- ks_cancel v11 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.061 → **harmful**
- nw_renewal v13 (controller_approved) dims=['voice'] engageΔ=-0.022 didΔ=+0.000 → **harmful**

**Interpretation.** Where we helped: **caller IDs** are the biggest single lever (KYC pickup +22–25%, Kestrel +8% / +37% on the
incident run). On public_02 the scripted spam flag was out of rotation within 2 hours, and spreading volume over
a pool beat concentrating it. **Variants**: renewal and Kestrel engagement +4% on public_01, and 14–39% of the hidden
engagement headroom captured across runs. The decaying W3 was promoted and rolled back on public_01 (share 0.85 → 0.46
on the day of detection → 0); on public_02 the recency check kept it from ever being promoted. **Incident**:
hallucinated calls on Kestrel 92 vs 310 baseline. Credits used: 6 of 200.

Where we were harmful, and why: (1) **probing unknown DIDs**: renewal's D4 failed its probe at −43% pickup, and we pay
a few hours of 10% traffic to find that out (public_01 v2, v7 = the probe and the one re-probe). That's the price of "explore
safely"; the alternative is never using spare numbers. (2) **A one-time re-probe** of Kestrel's spam-flagged D2
after its 72h rest (public_02). It exists because fatigued DIDs recover and flagged ones don't, and we can't tell
which is which without trying once. (3) **"Keep incumbent" at the sample cap on Kestrel welcome** (−0.011):
all arms were within ±0.8σ at the workflow level; truth apparently has segment effects below our z ≥ 3.2
segment bar. (4) On public_02, **moving renewal's voice off the supervisor's V2** after a significant holdback
signal (−0.022): the holdback arms were small, and we'd now require more holdback data before acting on
supervisor-chosen arms. (5) **KYC caller rebalance** (public_01 v4, −0.027): adding DIDs raised pickup but the
grader scores the step against the one-DID config at that moment.

What I'd change next: give a probe a 5% share where policy allows more steps; require a larger holdback sample
before moving off a supervisor's choice; and add W×V interaction cells once an experiment has concluded.

## 12. Trade-offs & rejected alternatives

| Option | Why considered | Why rejected / what instead |
|---|---|---|
| Thompson-sampling bandit on W/V | Less regret than A/B | Each allocation change is an approval request (3/day cap, latency); bandits want continuous updates. We use explore→exploit with harm stops, which needs 2–4 requests per experiment |
| Bayesian "probability to beat" as the main stopping rule | Natural for business readers | Uncorrected repeated looks inflate false winners. Fixed-look z ≥ 2.6 with a simulated FWER is easier to defend; Bayesian reasoning only at the final look (lean rule) |
| Crown a caller-ID winner (85%) | Same machinery as variants | Our data shows DIDs fatigue under concentration; the "winner" decayed in every run. Rotation pool instead |
| Per-call rows in the store | Compliance per-call audit | ~650k rows/day/platform-slice → aggregates + config-version lineage; the CDR is the per-call record |
| Auto-apply W/V on Kestrel (policy allows) | Faster lift | Customer-facing; supervisor asked to be told first; approvals there are fast anyway |
| LLM picks the fix | Fast | Forbidden by the contract, and it suggested the riskiest prompt first. Production evidence + validation decide |
| Postgres/Redis | Production standard | SRE: no new stateful infra for a prototype; schema is portable, the file is the whole state |
| Proactive prompt experiments | P3 "grounded" might lift engagement | Workflow-wide prompt → no concurrent control; a switchback design is possible but slow and customer-facing |

## 13. Known weaknesses

- **Guardrail vs the counterfactual.** On public_01 KYC, engagement was 0.588 against 0.612 in the grader's baseline run (−0.024), but only −0.006 against its own 14-day history (0.594) while pickup rose +22%. Against
  the workflow's own history the drop is within margin (our monitor didn't fire), but the grader's no-op run
  had a higher engagement period. Part of a pickup gain is mechanically "marginal pickers engage less", and
  without a permanent holdout we can't separate that from a DID effect.
- **Kestrel "keep incumbent" decisions graded harmful (~−0.01).** At the aggregate all arms were within ±0.8σ of
  W1/V1, so keeping the incumbent was the correct null decision. The grader's truth suggests segment-level
  effects we didn't have the power to see at z ≥ 3.2.
- **Probing a DID that turns out to be bad costs a few hours of worse pickup** (renewal D4 failed its probe at
  −43%), and the one re-probe of a spam-flagged DID costs a few hours more. All 8 renewal-type harmful changes
  on public_03 are exactly this.
- **No W×V interaction model;** a combination effect that cancels in the marginals is invisible.
- **Lean rule** may switch to an arm that is slightly worse (within the MDE) about as often as slightly better.
- **Single process, single writer.** Fine to ~10×, not beyond (§8).
- **Approver identity** is not in the API; "who approved" is an approval ID and a time.
- The **supervisor view is a CLI**, not a page a supervisor would actually open.
- **Decay near the end of a run.** On public_03 the renewal W3 decay became visible only on the last day; the
  self-decline check (z ≤ −3 on a 24h window that still contained the good day) hadn't fired before the
  scenario ended, so W3 stayed at 85% for the last two days.
- Thresholds were reviewed against public scenarios' grade output (allowed, but a risk of mild overfitting);
  none of them reads hidden truth.

## 14. If this went to production at Formi next month

1. **Week 1:** shadow mode on 3 real workflows: ingest the live stream, compute decisions, send nothing. Compare
   the decision log with what a human would have done, and check the DID-fatigue finding on real carrier data.
2. Move the store to Postgres (same schema), one controller per tenant shard, Prometheus metrics from
   `loop_metrics`, wire pages to on-call.
3. Per-call variant retention (CDR join) so Compliance's 24h answer goes down to the call.
4. Turn on caller-ID rotation first (operational, auto-applied, biggest measured lift), then experiments on one
   engagement workflow per tenant with the client's approver in the loop.
5. **Week 2:** supervisor web view over the `status` queries; weekly operational-change summary for Compliance;
   then C4 (hour weights per cohort) on the workflows whose policy auto-applies `schedule`.
