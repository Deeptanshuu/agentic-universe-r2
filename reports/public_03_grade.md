# Grade report — public_03_load

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| arav_cancel_2 | 0.398 | +16.8% | 0.259 | +5.9% | 0.542 | +8.9% | 56% | 442 (383) |
| arav_kyc_1 | 0.364 | +1.7% | 0.218 | +1.5% | 0.599 | -0.1% | 0% | 346 (349) |
| arav_renewal_0 | 0.472 | +16.8% | 0.282 | +6.9% | 0.596 | +8.8% | 52% | 453 (458) |
| kest_cancel_2 | 0.394 | +16.4% | 0.262 | +7.5% | 0.533 | +7.1% | 46% | 387 (388) |
| kest_kyc_1 | 0.483 | +36.2% | 0.279 | +30.7% | 0.596 | -0.2% | 0% | 435 (309) |
| kest_renewal_0 | 0.483 | +20.4% | 0.284 | +7.9% | 0.600 | +9.8% | 51% | 429 (430) |
| nort_cancel_2 | 0.394 | +16.1% | 0.260 | +6.6% | 0.535 | +7.6% | 51% | 445 (389) |
| nort_kyc_1 | 0.354 | -0.1% | 0.214 | +0.2% | 0.595 | -0.2% | 0% | 357 (346) |
| nort_renewal_0 | 0.473 | +17.9% | 0.282 | +6.7% | 0.595 | +9.1% | 47% | 442 (431) |
| tide_cancel_2 | 0.399 | +17.6% | 0.261 | +6.9% | 0.539 | +7.9% | 55% | 424 (406) |
| tide_kyc_1 | 0.483 | +36.1% | 0.280 | +31.3% | 0.592 | -0.9% | 0% | 475 (339) |
| tide_renewal_0 | 0.483 | +20.3% | 0.281 | +6.9% | 0.607 | +11.1% | 59% | 445 (422) |
| ALL | 0.432 | +18.1% | 0.263 | +9.6% | 0.578 | +6.0% | 34% | 5080 (4650) |

## Changes applied by the controller

74 applied — harmful: 8, neutral: 21, beneficial: 45

- nort_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.027 → **harmful**
- nort_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.012 → **neutral**
- kest_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.027 → **harmful**
- kest_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.012 → **neutral**
- arav_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.027 → **harmful**
- arav_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.012 → **neutral**
- tide_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.027 → **harmful**
- tide_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.012 → **neutral**
- nort_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.040 → **beneficial**
- arav_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.040 → **beneficial**
- tide_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.040 → **beneficial**
- kest_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.040 → **beneficial**
- nort_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.027 → **beneficial**
- nort_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.022 → **beneficial**
- kest_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.027 → **beneficial**
- kest_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.022 → **beneficial**
- arav_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.027 → **beneficial**
- arav_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.022 → **beneficial**
- tide_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.027 → **beneficial**
- tide_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.022 → **beneficial**
- nort_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.005 → **neutral**
- kest_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.005 → **neutral**
- arav_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.005 → **neutral**
- tide_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.005 → **neutral**
- kest_cancel_2 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- arav_cancel_2 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- tide_kyc_1 v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.215 → **beneficial**
- nort_cancel_2 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- tide_cancel_2 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- arav_renewal_0 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.246 → **beneficial**
- kest_cancel_2 v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.037 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.066 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.254 → **beneficial**
- arav_cancel_2 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.098 didΔ=+0.000 → **beneficial**
- tide_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.258 → **beneficial**
- tide_renewal_0 v6 (controller_approved) dims=['cohorts', 'welcome_message', 'voice'] engageΔ=+0.089 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v7 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.044 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.098 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.073 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.065 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v7 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v7 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.019 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v7 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.019 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v8 (controller_approved) dims=['voice'] engageΔ=+0.012 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.010 didΔ=+0.000 → **neutral**
- nort_renewal_0 v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v6 (controller_approved) dims=['cohorts', 'welcome_message', 'voice'] engageΔ=+0.091 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v8 (controller_approved) dims=['welcome_message'] engageΔ=+0.026 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v8 (controller_approved) dims=['welcome_message'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- nort_cancel_2 v9 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_cancel_2 v8 (controller_approved) dims=['welcome_message'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- kest_renewal_0 v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v10 (controller_approved) dims=['voice'] engageΔ=+0.012 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v8 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.042 → **harmful**
- kest_renewal_0 v8 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.042 → **harmful**
- arav_renewal_0 v8 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.042 → **harmful**
- tide_renewal_0 v8 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.042 → **harmful**
- nort_renewal_0 v9 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.044 → **beneficial**
- kest_renewal_0 v9 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.044 → **beneficial**
- arav_renewal_0 v9 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.044 → **beneficial**
- tide_renewal_0 v9 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.044 → **beneficial**
- kest_renewal_0 v10 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_renewal_0 v10 (controller_approved) dims=['voice'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- arav_kyc_1 v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.281 → **beneficial**
- kest_renewal_0 v11 (controller_approved) dims=['voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- nort_kyc_1 v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.206 → **beneficial**
- tide_kyc_1 v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.012 → **neutral**
- kest_kyc_1 v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.011 → **neutral**
- arav_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.301 → **beneficial**

## Governance

```
{
  "approval_requests": 44,
  "decisions": {
    "approved": 42,
    "rejected": 2,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 2
  },
  "applied_via_approval": 42,
  "auto_applied": 32,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "kest_kyc_1": 2,
    "tide_kyc_1": 2,
    "nort_renewal_0": 2,
    "nort_cancel_2": 3,
    "kest_renewal_0": 2,
    "kest_cancel_2": 2,
    "arav_renewal_0": 2,
    "arav_cancel_2": 2,
    "tide_renewal_0": 2,
    "tide_cancel_2": 2,
    "nort_kyc_1": 1,
    "arav_kyc_1": 2
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nort_renewal_0:welcome_message=W3": {
    "3": 1.0,
    "4": 1.0
  },
  "kest_renewal_0:welcome_message=W3": {
    "3": 1.0,
    "4": 1.0
  },
  "arav_renewal_0:welcome_message=W3": {
    "3": 1.0,
    "4": 1.0
  },
  "tide_renewal_0:welcome_message=W3": {
    "3": 1.0,
    "4": 1.0
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0