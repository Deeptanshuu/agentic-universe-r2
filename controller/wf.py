"""Workflow spec wrapper + config helpers (pure functions, no I/O)."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from .stats import normalise, tv_distance

ALLOC_DIMS = ("welcome_message", "voice", "caller_id")

# metric -> (success column, trials column)
METRICS = {"engagement_rate": ("eng", "conn"), "pickup_rate": ("conn", "n")}


@dataclass
class WF:
    id: str
    spec: dict

    @property
    def tenant(self) -> str:
        return self.spec.get("tenant_id", "")

    @property
    def options(self) -> dict:
        return self.spec["options"]

    @property
    def primary(self) -> str:
        return self.spec["objective"]["primary"]

    @property
    def guardrail(self) -> tuple[str, float]:
        g = self.spec["objective"].get("guardrail") or {}
        for k, v in g.items():  # e.g. pickup_rate_max_drop
            return k.replace("_max_drop", ""), float(v)
        return ("pickup_rate" if self.primary == "engagement_rate" else "engagement_rate"), 0.02

    @property
    def gov(self) -> dict:
        return self.spec["governance"]

    @property
    def window(self) -> tuple[int, int]:
        a, b = self.spec["dialling_window"]
        return int(a), int(b)

    @property
    def approver_hours(self) -> tuple[int, int]:
        a, b = self.gov["approver_hours"]
        return int(a), int(b)

    @property
    def next_campaign_only(self) -> bool:
        return self.spec.get("mid_flight") == "next_campaign_only"

    @property
    def cadence(self) -> list[int]:
        return list(self.spec.get("cadence") or [1])

    @property
    def batch_size(self) -> int:
        return int((self.spec.get("daily_batch") or {}).get("size", 0))

    @property
    def batch_hour(self) -> int:
        return int((self.spec.get("daily_batch") or {}).get("at_hour", 8))

    @property
    def min_pass(self) -> float:
        return float(self.gov.get("min_validation_pass_rate", 0.97))

    def experiment_dims(self) -> list[str]:
        """Variant experiments target levers that move this workflow's PRIMARY KPI. Engagement is moved by
        how the agent shows up (welcome, voice). Pickup is moved by who is calling — caller ID — which is
        managed by the rotation pool in health.py (a fatigable resource, not a variant to crown a winner
        for). We don't run variant experiments on dims that only touch the guardrail."""
        dims = ["welcome_message", "voice"] if self.primary == "engagement_rate" else []
        return [d for d in dims if len(self.options.get(d, [])) > 1]

    def auto_allowed(self, dims: set[str]) -> bool:
        aa = self.gov.get("auto_apply") or {}
        return bool(aa.get("enabled")) and bool(dims) and dims <= set(aa.get("allowed_dims") or [])

    @property
    def max_shift(self) -> float:
        return float((self.gov.get("auto_apply") or {}).get("max_shift", 0) or 0)

    def first_attempt_map(self) -> dict[int, int]:
        """campaign_day_index -> attempt_no of the first dial that day (to count unique leads/day)."""
        out, acc = {}, 0
        for i, c in enumerate(self.cadence):
            out[i] = acc + 1
            acc += c
        return out


def dist_of(cfg: dict, cohort: str, dim: str) -> dict:
    """Effective distribution for a cohort/dim (falls back to default like the dialler does)."""
    al = cfg.get("allocations", {})
    d = (al.get(cohort) or {}).get(dim)
    if d is None:
        d = (al.get("default") or {}).get(dim) or {}
    return normalise(d)


def majority(d: dict) -> str | None:
    if not d:
        return None
    return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def changed_dims(old: dict, new: dict) -> set[str]:
    out = set()
    if json.dumps(old.get("cohorts", []), sort_keys=True) != json.dumps(new.get("cohorts", []), sort_keys=True):
        out.add("cohorts")
    oa, na = old.get("allocations", {}), new.get("allocations", {})
    for dim in ALLOC_DIMS:
        for c in set(oa) | set(na):
            a = (oa.get(c) or {}).get(dim)
            b = (na.get(c) or {}).get(dim)
            if (a is None) != (b is None) or (a is not None and tv_distance(a, b) > 1e-9):
                out.add(dim)
    if old.get("prompt_version") != new.get("prompt_version"):
        out.add("prompt_version")
    if json.dumps(old.get("schedule", {}), sort_keys=True) != json.dumps(new.get("schedule", {}), sort_keys=True):
        out.add("schedule")
    return out


def max_shift_of(old: dict, new: dict) -> float:
    """Largest per-cohort TV shift over welcome/voice/caller (what auto-apply's max_shift limits)."""
    cohorts = {"default"} | {c["id"] for c in old.get("cohorts", [])} | {c["id"] for c in new.get("cohorts", [])}
    m = 0.0
    for c in cohorts:
        for dim in ALLOC_DIMS:
            m = max(m, tv_distance(dist_of(old, c, dim), dist_of(new, c, dim)))
    return m


def set_dist(cfg: dict, cohort: str, dim: str, dist: dict | None) -> None:
    al = cfg.setdefault("allocations", {})
    if dist is None:
        if cohort in al:
            al[cohort].pop(dim, None)
        return
    al.setdefault(cohort, {})[dim] = {k: round(v, 4) for k, v in sorted(dist.items()) if v > 0}


def diff_summary(old: dict, new: dict) -> dict:
    """Human-readable diff: path -> {from, to}."""
    out = {}
    oa, na = old.get("allocations", {}), new.get("allocations", {})
    for c in sorted(set(oa) | set(na)):
        for dim in ALLOC_DIMS:
            a = (oa.get(c) or {}).get(dim)
            b = (na.get(c) or {}).get(dim)
            if a != b and not (a and b and tv_distance(a, b) < 1e-9):
                out[f"allocations.{c}.{dim}"] = {"from": a, "to": b}
    if old.get("prompt_version") != new.get("prompt_version"):
        out["prompt_version"] = {"from": old.get("prompt_version"), "to": new.get("prompt_version")}
    oc = {c["id"]: c for c in old.get("cohorts", [])}
    nc = {c["id"]: c for c in new.get("cohorts", [])}
    for cid in sorted(set(oc) | set(nc)):
        if oc.get(cid) != nc.get(cid):
            out[f"cohorts.{cid}"] = {"from": oc.get(cid), "to": nc.get(cid)}
    if json.dumps(old.get("schedule", {}), sort_keys=True) != json.dumps(new.get("schedule", {}), sort_keys=True):
        out["schedule"] = {"changed": True}
    return out


def step_towards(live: dict, target: dict, max_tv: float) -> dict:
    """Move a distribution towards target by at most max_tv total-variation (for auto-apply max_shift)."""
    tv = tv_distance(live, target)
    if tv <= max_tv + 1e-9:
        return dict(target)
    f = max_tv / tv
    keys = set(live) | set(target)
    out = {k: live.get(k, 0) + f * (target.get(k, 0) - live.get(k, 0)) for k in keys}
    return {k: round(v, 4) for k, v in out.items() if v > 1e-4}


def clone(cfg: dict) -> dict:
    return copy.deepcopy(cfg)
