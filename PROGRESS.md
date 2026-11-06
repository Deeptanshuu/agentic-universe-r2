# Progress checkpoint

- [x] Controller v0 end-to-end on public_01 (1f730b6)
- [x] Caller-ID rotation pool, recency checks, final-look rule, guardrails (be2cc47)
- [x] Tests: stats (FWER 6.3% by Monte-Carlo), governance reconciliation, opt-in crash integration (e1dfb98)
- [x] Load-scenario fixes: decay sensor never removed, last-DID replacement (0c25db8)
- [x] Final grades: public_01 +13.6%, public_02 +27.1%, public_03 +19.6% (reports/)
- [x] Load numbers: 47–53 s wall per simulated day, ~39k events/s, 290 MB peak RSS (reports/public_03_load.md)
- [x] Crash recovery: 10 SIGKILLs, 7/7 invariants, identical grade (reports/crash_recovery.md)
- [x] SUBMISSION.md, docs/RUNBOOK.md

Open / next: per-person call time (C4), W×V interaction cells, supervisor web view, larger holdback before
moving off a supervisor-chosen arm.
