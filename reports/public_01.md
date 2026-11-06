# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.388 | +13.5% | 0.259 | +8.0% | 0.519 | +3.8% | 34% | 55 (52) |
| nw_kyc | 0.453 | +20.3% | 0.296 | +22.4% | 0.591 | -3.5% | 0% | 39 (21) |
| nw_renewal | 0.457 | +8.6% | 0.273 | +4.2% | 0.572 | +4.5% | 34% | 37 (39) |
| ALL | 0.425 | +13.1% | 0.271 | +9.3% | 0.553 | +2.6% | 25% | 131 (112) |

## Changes applied by the controller

23 applied — harmful: 2, neutral: 10, beneficial: 11

- nw_renewal v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.025 → **harmful**
- ks_cancel v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.013 → **neutral**
- ks_cancel v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- nw_renewal v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- nw_renewal v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.038 → **beneficial**
- nw_kyc v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.201 → **beneficial**
- ks_cancel v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.033 → **beneficial**
- ks_cancel v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.007 → **neutral**
- nw_renewal v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.028 → **beneficial**
- ks_cancel v6 (controller_approved) dims=['voice'] engageΔ=+0.054 didΔ=+0.000 → **beneficial**
- nw_renewal v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.066 didΔ=+0.000 → **beneficial**
- nw_renewal v7 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.041 → **harmful**
- ks_cancel v7 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v8 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v8 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.042 → **beneficial**
- nw_kyc v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.045 → **beneficial**
- nw_renewal v9 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.011 → **neutral**
- ks_cancel v9 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.028 didΔ=+0.000 → **beneficial**
- nw_renewal v10 (controller_approved) dims=['welcome_message'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- nw_renewal v11 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v10 (controller_approved) dims=['welcome_message'] engageΔ=+0.010 didΔ=+0.000 → **neutral**
- nw_renewal v12 (controller_approved) dims=['welcome_message'] engageΔ=+0.105 didΔ=+0.000 → **beneficial**
- nw_renewal v13 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.012 → **neutral**

## Governance

```
{
  "approval_requests": 17,
  "decisions": {
    "approved": 16,
    "rejected": 1,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 13,
  "auto_applied": 10,
  "auto_apply_rejected": 0,
  "apply_conflicts": 3,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_kyc": 1,
    "nw_renewal": 2,
    "ks_cancel": 2
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
  "nw_renewal:welcome_message=W3": {
    "3": 0.847,
    "4": 0.847,
    "5": 0.473,
    "6": 0.0,
    "7": 0.0
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0