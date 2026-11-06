"""Every tunable in one place, with the reason it has the value it has. See SUBMISSION.md §5."""

TICK_MINUTES = 15

# Experiment decisions are only taken at fixed "looks" (sim hour, IST). A fixed look schedule is what makes
# the sequential false-winner rate computable (tests/test_stats.py measures it). 11:00 respects the
# Kestrel supervisor's "never before 11:00" and leaves time for approver latency before approver close.
LOOK_HOURS = (11, 15)

# ---- experiment design ------------------------------------------------------------------------------
EXPLORE_INCUMBENT_SHARE = 0.4  # incumbent keeps >=40% during exploration: bounded downside
EXPLOIT_HOLDBACK = 0.15        # after promotion 15% stays on the other arms: concurrent control for decay
Z_WIN = 2.6                    # one-sided, per challenger, at every look (Monte-Carlo FWER ~5%, see tests)
Z_LOSE = 2.6                   # drop an arm that is this significantly WORSE than incumbent (harm stop)
Z_DECAY = 2.33                 # champion significantly worse than pooled holdback in the trailing window
Z_DID = 3.0                    # champion's own rate fell vs its promotion window, net of control drift
DID_MIN_DROP = 0.05
Z_SEG = 3.2                    # segment-level override: Bonferroni over ~14 segments x 3 arms
MIN_N = {"engagement_rate": 300, "pickup_rate": 1000}      # trials/arm before any win/lose call
MIN_N_SEG = {"engagement_rate": 150, "pickup_rate": 600}
EXPLORE_MAX_N = {"engagement_rate": 1300, "pickup_rate": 5000}  # ~MDE 5pp / 2.5pp at 80% power
MAX_EXPLORE_HOURS = 96
DECAY_WINDOW_H = 36
MIN_CTRL_DECAY = {"engagement_rate": 80, "pickup_rate": 300}
MIN_SEG_SHARE = 0.10           # a cohort must carry >=10% of traffic or it fragments the experiment
MAX_COHORTS = 4
SUPERVISOR_PAUSE_H = 24        # after a supervisor edits a dim we leave that dim alone this long
MATERIAL_TV = 0.02             # smaller allocation moves are not worth a request

# ---- caller-ID health ------------------------------------------------------------------------------
CID_WINDOW_H = 6
CID_MIN_N = 150
Z_CID = 4.0                    # evaluated every tick on every DID: strict to keep false alarms rare
CID_REL_DROP = 0.20
CID_BASELINE_DAYS = 7

# ---- hallucination ---------------------------------------------------------------------------------
HAL_WINDOW_H = 8
HAL_MIN_CONN = 100
HAL_MIN_EVENTS = 6
HAL_RATIO = 2.0
Z_HAL = 4.0
Z_HAL_WATCH = 3.5              # used for 12h after a supervisor prompt change (prior is higher)
HAL_DEFAULT_BASE = 0.015
VALIDATION_N = 400             # 4 credits; Wilson LB separates ~0.99 from ~0.975 pass rates
MAX_VALIDATIONS_PER_INCIDENT = 3
CREDIT_RESERVE_FRAC = 0.15     # never spend below this share of the starting budget
MAX_PROMPT_FIXES_PER_DAY = 2   # then page a human instead of fighting the supervisor
VERIFY_MIN_CONN = 150

# ---- governance ------------------------------------------------------------------------------------
REQUEST_RESERVE = 1            # keep one approval request per 24h free for incidents (if cap >= 2)
APPROVAL_SUBMIT_LAST_HOUR_MARGIN = 1  # don't send routine requests in the approver's last hour
# Stakeholder 04 (Kestrel ops supervisor): <=2 requests/day, never before 11:00. Stricter than the policy
# (4/24h, 10-19) so we honour it for routine changes; incidents may still use the policy limit.
SUPERVISOR_PREFS = {"ks_cancel": {"max_requests_per_day": 2, "not_before_hour": 11}}
