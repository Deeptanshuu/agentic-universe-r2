# Grade report — public_02_incidents

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.363 | +48.7% | 0.250 | +36.9% | 0.495 | +4.9% | 39% | 92 (310) |
| nw_kyc | 0.487 | +26.7% | 0.309 | +24.7% | 0.594 | -0.9% | 0% | 44 (38) |
| nw_renewal | 0.475 | +10.5% | 0.285 | +8.5% | 0.552 | +0.8% | 14% | 51 (58) |
| ALL | 0.427 | +27.1% | 0.274 | +22.7% | 0.537 | +1.3% | 27% | 187 (406) |

## Changes applied by the controller

27 applied — beneficial: 14, neutral: 10, harmful: 3

- nw_renewal v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.025 → **beneficial**
- ks_cancel v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.013 → **neutral**
- nw_renewal v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.039 didΔ=+0.000 → **beneficial**
- ks_cancel v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.020 didΔ=+0.000 → **beneficial**
- nw_kyc v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.198 → **beneficial**
- ks_cancel v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.033 → **beneficial**
- ks_cancel v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.007 → **neutral**
- nw_renewal v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.021 → **beneficial**
- nw_renewal v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.011 → **neutral**
- nw_kyc v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.156 → **beneficial**
- ks_cancel v6 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.162 → **beneficial**
- ks_cancel v7 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.105 → **beneficial**
- nw_renewal v6 (controller_approved) dims=['voice'] engageΔ=+0.023 didΔ=+0.000 → **beneficial**
- ks_cancel v9 (controller_approved) dims=['prompt_version'] engageΔ=+0.102 didΔ=+0.000 → **beneficial**
- nw_renewal v7 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.004 → **neutral**
- nw_renewal v8 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.017 didΔ=+0.000 → **beneficial**
- ks_cancel v10 (controller_approved) dims=['welcome_message'] engageΔ=-0.011 didΔ=+0.000 → **harmful**
- ks_cancel v11 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.061 → **harmful**
- ks_cancel v12 (controller_approved) dims=['voice'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- nw_kyc v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.006 → **neutral**
- ks_cancel v13 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.065 → **beneficial**
- nw_renewal v10 (controller_approved) dims=['voice'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- nw_renewal v11 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.006 → **neutral**
- ks_cancel v14 (controller_approved) dims=['welcome_message'] engageΔ=+0.037 didΔ=+0.000 → **beneficial**
- nw_renewal v12 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.009 → **neutral**
- nw_kyc v5 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.010 → **neutral**
- nw_renewal v13 (controller_approved) dims=['voice'] engageΔ=-0.022 didΔ=+0.000 → **harmful**

## Governance

```
{
  "approval_requests": 16,
  "decisions": {
    "rejected": 2,
    "approved": 14,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 2
  },
  "applied_via_approval": 14,
  "auto_applied": 13,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_kyc": 1,
    "nw_renewal": 1,
    "ks_cancel": 2
  }
}
```

## Incidents

```
[
  {
    "type": "did_flagged",
    "workflow": "ks_cancel",
    "at": "D2 10:00",
    "flagged_did_share_by_day": {
      "2": 0.084,
      "3": 0.0,
      "4": 0.0,
      "5": 0.032,
      "6": 0.0,
      "7": 0.0,
      "8": 0.0,
      "9": 0.0
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "ks_cancel",
    "at": "D3 11:30",
    "mttr_minutes": 165,
    "fixed_by": "controller_approved"
  },
  {
    "type": "supervisor_edit",
    "workflow": "nw_renewal",
    "at": "D5 15:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "2": 0.301,
    "3": 0.302,
    "4": 0.224,
    "5": 0.079,
    "6": 0.074,
    "7": 0.078,
    "8": 0.07,
    "9": 0.074
  }
}
```

Credits used: 6 · validation runs: 1 · LLM fix calls: 0