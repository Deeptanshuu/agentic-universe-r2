# Grade report — public_03_load

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| arav_cancel_2 | 0.383 | +12.5% | 0.262 | +6.8% | 0.520 | +4.5% | 33% | 437 (383) |
| arav_kyc_1 | 0.466 | +30.2% | 0.270 | +25.7% | 0.595 | -0.7% | 0% | 404 (349) |
| arav_renewal_0 | 0.470 | +16.3% | 0.282 | +7.1% | 0.590 | +7.6% | 42% | 469 (458) |
| kest_cancel_2 | 0.392 | +15.9% | 0.261 | +6.9% | 0.532 | +7.0% | 43% | 414 (388) |
| kest_kyc_1 | 0.425 | +19.6% | 0.249 | +16.5% | 0.596 | -0.2% | 0% | 441 (309) |
| kest_renewal_0 | 0.474 | +18.2% | 0.284 | +8.1% | 0.594 | +8.8% | 47% | 456 (430) |
| nort_cancel_2 | 0.391 | +15.4% | 0.262 | +7.4% | 0.529 | +6.3% | 45% | 420 (389) |
| nort_kyc_1 | 0.462 | +30.4% | 0.269 | +25.9% | 0.594 | -0.4% | 0% | 439 (346) |
| nort_renewal_0 | 0.480 | +19.8% | 0.283 | +7.2% | 0.603 | +10.6% | 57% | 475 (431) |
| tide_cancel_2 | 0.390 | +14.9% | 0.260 | +6.2% | 0.533 | +6.9% | 47% | 429 (406) |
| tide_kyc_1 | 0.439 | +23.7% | 0.257 | +20.3% | 0.595 | -0.4% | 0% | 431 (339) |
| tide_renewal_0 | 0.474 | +18.1% | 0.280 | +6.6% | 0.598 | +9.6% | 52% | 426 (422) |
| ALL | 0.437 | +19.6% | 0.268 | +11.5% | 0.574 | +5.3% | 30% | 5241 (4650) |

## Changes applied by the controller

62 applied — harmful: 10, neutral: 16, beneficial: 36

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
- arav_cancel_2 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- nort_cancel_2 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- kest_renewal_0 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- tide_cancel_2 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- nort_renewal_0 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.060 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.040 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.066 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.066 didΔ=+0.000 → **beneficial**
- arav_kyc_1 v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.494 → **beneficial**
- kest_kyc_1 v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.198 → **beneficial**
- nort_kyc_1 v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.494 → **beneficial**
- tide_kyc_1 v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.198 → **beneficial**
- nort_cancel_2 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.097 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v7 (controller_approved) dims=['voice'] engageΔ=+0.055 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.072 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v7 (controller_approved) dims=['cohorts', 'voice'] engageΔ=+0.023 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.098 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v6 (controller_approved) dims=['cohorts', 'welcome_message', 'voice'] engageΔ=+0.090 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v7 (controller_approved) dims=['voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- arav_renewal_0 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.065 didΔ=+0.000 → **beneficial**
- tide_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.269 → **beneficial**
- kest_renewal_0 v8 (controller_approved) dims=['voice'] engageΔ=-0.000 didΔ=+0.000 → **neutral**
- nort_renewal_0 v8 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.042 → **harmful**
- kest_renewal_0 v9 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.042 → **harmful**
- arav_renewal_0 v7 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.042 → **harmful**
- tide_renewal_0 v7 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.042 → **harmful**
- nort_renewal_0 v9 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.044 → **beneficial**
- kest_renewal_0 v10 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.044 → **beneficial**
- arav_renewal_0 v8 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.044 → **beneficial**
- tide_renewal_0 v8 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.044 → **beneficial**
- kest_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.223 → **beneficial**
- arav_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.048 → **harmful**
- nort_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.046 → **harmful**
- nort_kyc_1 v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.017 → **neutral**
- arav_kyc_1 v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.016 → **neutral**

## Governance

```
{
  "approval_requests": 34,
  "decisions": {
    "rejected": 4,
    "approved": 30,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 4
  },
  "applied_via_approval": 30,
  "auto_applied": 32,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nort_kyc_1": 2,
    "arav_kyc_1": 2,
    "kest_kyc_1": 2,
    "tide_kyc_1": 1,
    "nort_renewal_0": 2,
    "nort_cancel_2": 1,
    "kest_renewal_0": 2,
    "kest_cancel_2": 1,
    "arav_renewal_0": 1,
    "arav_cancel_2": 1,
    "tide_renewal_0": 1,
    "tide_cancel_2": 2
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
    "3": 0.854,
    "4": 0.848
  },
  "kest_renewal_0:welcome_message=W3": {
    "3": 0.851,
    "4": 0.851
  },
  "arav_renewal_0:welcome_message=W3": {
    "3": 0.848,
    "4": 0.851
  },
  "tide_renewal_0:welcome_message=W3": {
    "3": 0.852,
    "4": 0.849
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0