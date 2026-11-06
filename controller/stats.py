"""Deterministic statistics used for every decision the controller makes.

Nothing in here is random and nothing calls an LLM: given the same counts, every function returns the same
answer. That is what "winner selection is statistical and deterministic" means in practice.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

_N = NormalDist()


def z_for(p_one_sided: float) -> float:
    """Critical z for a one-sided tail probability."""
    return _N.inv_cdf(1.0 - p_one_sided)


@dataclass(frozen=True)
class Cmp:
    """Comparison of arm A against arm B: diff = rate_A - rate_B (stratified)."""

    diff: float
    se: float
    n_a: int
    n_b: int
    s_a: int
    s_b: int

    @property
    def z(self) -> float:
        if self.se <= 0 or math.isinf(self.se):
            return 0.0
        return self.diff / self.se

    @property
    def p_a(self) -> float:
        return self.s_a / self.n_a if self.n_a else float("nan")

    @property
    def p_b(self) -> float:
        return self.s_b / self.n_b if self.n_b else float("nan")

    def lcb(self, z: float = 1.645) -> float:
        return self.diff - z * self.se

    def ucb(self, z: float = 1.645) -> float:
        return self.diff + z * self.se

    def as_dict(self) -> dict:
        return {"diff": round(self.diff, 4), "se": round(self.se, 4), "z": round(self.z, 2),
                "n_a": self.n_a, "n_b": self.n_b, "rate_a": _r(self.p_a), "rate_b": _r(self.p_b)}


def _r(x: float) -> float | None:
    return None if x != x else round(x, 4)


EMPTY = Cmp(0.0, float("inf"), 0, 0, 0, 0)


def stratified_diff(strata) -> Cmp:
    """Cochran–Mantel–Haenszel style risk difference of A vs B across strata.

    strata: iterable of (succ_a, n_a, succ_b, n_b). A stratum is one (day, config_version, cohort): inside
    it the allocation was fixed, so arms were randomised against each other and the comparison is fair.
    Pooling raw counts across strata would be confounded whenever allocation shares moved between days
    (Simpson's paradox); weighting within-stratum differences avoids that. Strata with only one arm carry no
    comparative information and get weight 0.
    """
    w_sum = num = var = 0.0
    na_t = nb_t = sa_t = sb_t = 0
    for sa, na, sb, nb in strata:
        na_t += na
        nb_t += nb
        sa_t += sa
        sb_t += sb
        if na <= 0 or nb <= 0:
            continue
        w = na * nb / (na + nb)
        pa_s = (sa + 0.5) / (na + 1.0)  # smoothed only for the variance term
        pb_s = (sb + 0.5) / (nb + 1.0)
        num += w * (sa / na - sb / nb)
        var += w * w * (pa_s * (1 - pa_s) / na + pb_s * (1 - pb_s) / nb)
        w_sum += w
    if w_sum <= 0:
        return Cmp(0.0, float("inf"), na_t, nb_t, sa_t, sb_t)
    return Cmp(num / w_sum, math.sqrt(var) / w_sum, na_t, nb_t, sa_t, sb_t)


def n_per_arm(p: float, mde: float, alpha_one_sided: float = 0.05, power: float = 0.8) -> int:
    """Per-arm sample size for a two-proportion test detecting an absolute difference `mde`."""
    za, zb = z_for(alpha_one_sided), _N.inv_cdf(power)
    return int(math.ceil(2 * (za + zb) ** 2 * p * (1 - p) / (mde * mde)))


def wilson_lower(successes: int, n: int, z: float = 1.645) -> float:
    if n <= 0:
        return 0.0
    p = successes / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - r) / d


def excess_rate_z(x: int, n: int, base: float) -> float:
    """One-sided z for 'x events in n trials is above the base rate'."""
    if n <= 0 or base <= 0:
        return 0.0
    exp = n * base
    return (x - exp) / math.sqrt(exp * (1 - base))


def shrunk_rate(s: int, n: int, prior_rate: float, prior_n: float = 50.0) -> float:
    """Beta-binomial posterior mean; used only for dry-run estimates, never for picking winners."""
    return (s + prior_rate * prior_n) / (n + prior_n)


def tv_distance(a: dict, b: dict) -> float:
    """Total-variation distance between two (unnormalised) categorical weight maps."""
    sa = sum(a.values()) or 1.0
    sb = sum(b.values()) or 1.0
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0) / sa - b.get(k, 0) / sb) for k in keys)


def normalise(d: dict) -> dict:
    s = sum(v for v in d.values() if v > 0)
    if s <= 0:
        return {}
    return {k: round(v / s, 4) for k, v in sorted(d.items()) if v > 0}
