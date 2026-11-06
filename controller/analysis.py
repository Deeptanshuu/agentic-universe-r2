"""Read-side queries over the aggregates. Every number a decision uses comes through here."""
from __future__ import annotations

from collections import defaultdict

from .stats import Cmp, stratified_diff
from .wf import METRICS

_DAY = "((hb + 240000) / 24 - 10000)"  # floor(hb/24) that also works for negative (history) buckets


class ArmData:
    """{arm: {stratum: [n, conn, eng]}} for one (workflow, dim, segment, window, cohort set)."""

    def __init__(self, rows):
        self.by_arm: dict[str, dict[tuple, list[int]]] = defaultdict(dict)
        for arm, day, ver, coh, n, conn, eng in rows:
            self.by_arm[arm][(day, ver, coh)] = [n, conn, eng]

    def arms(self) -> list[str]:
        return sorted(self.by_arm)

    def totals(self, arm: str) -> tuple[int, int, int]:
        n = c = e = 0
        for v in self.by_arm.get(arm, {}).values():
            n += v[0]; c += v[1]; e += v[2]  # noqa: E702
        return n, c, e

    def trials(self, arm: str, metric: str) -> int:
        n, c, e = self.totals(arm)
        return {"n": n, "conn": c}[METRICS[metric][1]]

    def rate(self, arm: str, metric: str) -> float | None:
        n, c, e = self.totals(arm)
        s, t = {"engagement_rate": (e, c), "pickup_rate": (c, n)}[metric]
        return s / t if t else None

    def _st(self, arm: str, stratum: tuple, metric: str) -> tuple[int, int]:
        v = self.by_arm.get(arm, {}).get(stratum)
        if not v:
            return 0, 0
        return (v[2], v[1]) if metric == "engagement_rate" else (v[1], v[0])

    def cmp(self, a: str, b: str, metric: str) -> Cmp:
        strata = set(self.by_arm.get(a, {})) | set(self.by_arm.get(b, {}))
        rows = []
        for s in strata:
            sa, na = self._st(a, s, metric)
            sb, nb = self._st(b, s, metric)
            rows.append((sa, na, sb, nb))
        return stratified_diff(rows)

    def cmp_vs_pool(self, a: str, others: list[str], metric: str) -> Cmp:
        strata = set(self.by_arm.get(a, {}))
        for o in others:
            strata |= set(self.by_arm.get(o, {}))
        rows = []
        for s in strata:
            sa, na = self._st(a, s, metric)
            sb = nb = 0
            for o in others:
                x, y = self._st(o, s, metric)
                sb += x
                nb += y
            rows.append((sa, na, sb, nb))
        return stratified_diff(rows)


def arm_data(store, wf: str, dim: str, seg: str, hb_from: int, hb_to: int | None = None,
             cohorts: list[str] | None = None) -> ArmData:
    sql = (f"SELECT arm, {_DAY} AS day, ver, cohort, SUM(n), SUM(conn), SUM(eng) FROM arm_stats "
           "WHERE wf=? AND dim=? AND seg=? AND hb>=?")
    args: list = [wf, dim, seg, hb_from]
    if hb_to is not None:
        sql += " AND hb<?"
        args.append(hb_to)
    if cohorts is not None:
        sql += f" AND cohort IN ({','.join('?' * len(cohorts))})"
        args += cohorts
    sql += " GROUP BY arm, day, ver, cohort"
    return ArmData(store.q(sql, args))


def segments(store, wf: str, dim: str, feature: str, hb_from: int) -> list[str]:
    rows = store.q("SELECT DISTINCT seg FROM arm_stats WHERE wf=? AND dim=? AND seg LIKE ? AND hb>=?",
                   (wf, dim, feature + "=%", hb_from))
    return sorted(r[0] for r in rows)


def kpis(store, wf: str, hb_from: int, hb_to: int | None = None, cohorts: list[str] | None = None) -> dict:
    sql = "SELECT SUM(n), SUM(conn), SUM(eng), SUM(hal), SUM(uniq) FROM kpi_stats WHERE wf=? AND hb>=?"
    args: list = [wf, hb_from]
    if hb_to is not None:
        sql += " AND hb<?"
        args.append(hb_to)
    if cohorts is not None:
        sql += f" AND cohort IN ({','.join('?' * len(cohorts))})"
        args += cohorts
    n, c, e, h, u = store.q1(sql, args)
    n, c, e, h, u = n or 0, c or 0, e or 0, h or 0, u or 0
    return {"dials": n, "connected": c, "engaged": e, "hallucinated": h, "unique_leads": u,
            "pickup_rate": round(c / n, 4) if n else None,
            "engagement_rate": round(e / c, 4) if c else None,
            "halluc_rate": round(h / c, 4) if c else None}


def cohort_share(store, wf: str, cohorts: list[str], hb_from: int) -> float:
    tot = store.q1("SELECT SUM(n) FROM kpi_stats WHERE wf=? AND hb>=?", (wf, hb_from))[0] or 0
    if not tot:
        return 0.0
    part = store.q1(f"SELECT SUM(n) FROM kpi_stats WHERE wf=? AND hb>=? AND cohort IN "
                    f"({','.join('?' * len(cohorts))})", [wf, hb_from] + cohorts)[0] or 0
    return part / tot


def segment_share(store, wf: str, seg: str, hb_from: int) -> float:
    tot = store.q1("SELECT SUM(n) FROM arm_stats WHERE wf=? AND dim='voice' AND seg='all' AND hb>=?",
                   (wf, hb_from))[0] or 0
    part = store.q1("SELECT SUM(n) FROM arm_stats WHERE wf=? AND dim='voice' AND seg=? AND hb>=?",
                    (wf, seg, hb_from))[0] or 0
    return part / tot if tot else 0.0
