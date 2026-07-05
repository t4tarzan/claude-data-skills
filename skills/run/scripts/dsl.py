"""dsl.py — the governed metric DSL: definition shape, golden-query compiler, and the
re-aggregation governance guard.

Ports the correctness heart of AiNa's dsl_integration: a measure's aggregation class decides
whether it may be SUMMED up a grain or must be RECOMPUTED. Additive measures (sum/min/max/
count) roll up by summing; ratios/averages recompute from components; distinct-counts are
NOT summable across grains and require a sketch (Theta/HLL). This guard is what makes the
semantic layer *governed* rather than a pile of SQL.

Stdlib only. Metric definitions are JSON dicts (YAML optional via PyYAML if present).
"""

from __future__ import annotations

from typing import Any

# aggregation classes
ADDITIVE = {"sum", "min", "max", "count"}
RATIO_LIKE = {"ratio", "avg"}
NONADD_DISTINCT = {"count_distinct"}
ALL_AGG = ADDITIVE | RATIO_LIKE | NONADD_DISTINCT | {"derived"}

_GRAIN_ORDER = {"daily": 0, "weekly": 1, "monthly": 2, "quarterly": 3, "yearly": 4}


def golden_query(m: dict) -> str:
    """Compile a metric definition to its SQL aggregate expression over its rollup table."""
    agg = m.get("aggregation")
    meas = m.get("measure")
    if agg == "sum":
        return f"sum({_c(meas)})"
    if agg == "count":
        return f"count({_c(meas)})" if meas else "count(*)"
    if agg == "count_distinct":
        return f"count(distinct {_c(meas)})"
    if agg in ("min", "max", "avg"):
        return f"{agg}({_c(meas)})"
    if agg == "ratio":
        num, den = m.get("numerator"), m.get("denominator")
        return f"sum({_c(num)})*1.0/nullif(sum({_c(den)}),0)"
    if agg == "derived":
        return m.get("expression", "") or m.get("golden_query", "")
    raise ValueError(f"unknown aggregation: {agg!r}")


def _c(name: str | None) -> str:
    if not name:
        raise ValueError("metric measure/component missing")
    return '"' + str(name).replace('"', '""') + '"'


def validate_metric(m: dict) -> list[str]:
    """Structural validation — returns a list of problem strings (empty = valid)."""
    issues: list[str] = []
    if not m.get("metric"):
        issues.append("missing 'metric' id")
    agg = m.get("aggregation")
    if agg not in ALL_AGG:
        issues.append(f"aggregation {agg!r} not in {sorted(ALL_AGG)}")
    if agg in ADDITIVE | NONADD_DISTINCT and not m.get("measure"):
        issues.append(f"aggregation {agg!r} requires 'measure'")
    if agg == "ratio" and not (m.get("numerator") and m.get("denominator")):
        issues.append("ratio requires 'numerator' and 'denominator'")
    if agg == "derived" and not (m.get("expression") or m.get("golden_query")):
        issues.append("derived requires 'expression'")
    if not (m.get("rollups") or {}).get("daily"):
        issues.append("missing rollups.daily (the bound gold table)")
    return issues


def reaggregation_verdict(agg: str, from_grain: str = "daily", to_grain: str = "monthly") -> dict[str, Any]:
    """The governance guard. Can a measure of class `agg` roll up from `from_grain` to `to_grain`
    by SUMMING? If not, what is required? This is the check AiNa trips for `buyers · monthly`.
    """
    coarser = _GRAIN_ORDER.get(to_grain, 99) > _GRAIN_ORDER.get(from_grain, 0)
    if not coarser:
        return {"ok": True, "strategy": "exact", "reason": f"{to_grain} is not coarser than {from_grain} — exact"}
    if agg in ADDITIVE:
        return {"ok": True, "strategy": "sum", "reason": f"additive ({agg}) — safe to sum up the grain"}
    if agg in RATIO_LIKE:
        how = "sum(num)/sum(den)" if agg == "ratio" else "sum/count"
        return {"ok": False, "strategy": "recompute", "guard": "non-additive",
                "reason": f"{agg} is not summable — recompute from components ({how})"}
    if agg in NONADD_DISTINCT:
        return {"ok": False, "strategy": "sketch", "guard": "non-additive-distinct",
                "reason": f"distinct-count is not summable across {from_grain}->{to_grain} — use a Theta/HLL sketch"}
    return {"ok": False, "strategy": "recompute-derived", "guard": "derived",
            "reason": "derived metric — recompute from its component metrics at the target grain"}


def aggregation_class(agg: str) -> str:
    if agg in ADDITIVE:
        return "additive"
    if agg in RATIO_LIKE:
        return "ratio-like"
    if agg in NONADD_DISTINCT:
        return "non-additive-distinct"
    return "derived"


def load_metric_file(path) -> dict:
    """Load a metric def from .json (stdlib) or .yml/.yaml (needs PyYAML; raises if absent)."""
    import json
    import pathlib
    p = pathlib.Path(path)
    if p.suffix.lower() in (".yml", ".yaml"):
        try:
            import yaml  # optional
        except ImportError as e:
            raise RuntimeError(f"{p.name}: YAML metric defs need PyYAML (pip install pyyaml)") from e
        return yaml.safe_load(p.read_text())
    return json.loads(p.read_text())
