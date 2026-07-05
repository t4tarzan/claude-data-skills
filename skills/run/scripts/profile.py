"""profile.py — type inference + column profiling + PII heuristics for the Data Architect.

Given raw string rows from an adapter, decide each column's type, detect candidate keys,
compute a null/quality profile, and flag likely-PII columns. Deterministic and stdlib-only
(the Architect stage is code, not a model — docs/04-engine-and-sovereignty.md).
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_BOOL = {"true", "false", "yes", "no", "y", "n", "0", "1", "t", "f"}
_DATE_FMTS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y", "%Y%m%d")
_DATETIME_FMTS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ")

# PII: name-pattern OR value-pattern. Conservative — flags for review, does not auto-drop.
_PII_NAME = {
    "email": re.compile(r"e[-_]?mail", re.I),
    "phone": re.compile(r"phone|mobile|msisdn|contact_no", re.I),
    "name": re.compile(r"(full|first|last|customer|user)[_ ]?name|^name$", re.I),
    "address": re.compile(r"address|street|pincode|zip|postal", re.I),
    "id": re.compile(r"aadhaar|ssn|pan|passport|national[_ ]?id", re.I),
}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?\d[\d\s\-]{7,}\d$")


def _is_date(s: str) -> bool:
    for fmt in _DATE_FMTS:
        try:
            _dt.datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


def _is_datetime(s: str) -> bool:
    for fmt in _DATETIME_FMTS:
        try:
            _dt.datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


def infer_column_type(values: list[str]) -> str:
    """Infer a SQL-ish type from non-empty string samples: integer|real|boolean|date|datetime|text."""
    vals = [v for v in values if v is not None and v.strip() != ""]
    if not vals:
        return "text"
    checks = {
        "integer": all(_INT_RE.match(v) for v in vals),
        "real": all(_FLOAT_RE.match(v) for v in vals),
        "boolean": all(v.strip().lower() in _BOOL for v in vals),
        "datetime": all(_is_datetime(v) for v in vals),
        "date": all(_is_date(v) for v in vals),
    }
    # boolean only if it also looks boolean-ish (avoid classifying a 0/1 id column as bool blindly:
    # require at least one non-numeric boolean token OR <=2 distinct values)
    if checks["boolean"] and (any(v.strip().lower() in {"true", "false", "yes", "no", "t", "f", "y", "n"} for v in vals)
                              or len(set(v.lower() for v in vals)) <= 2 and not checks["integer"]):
        return "boolean"
    if checks["integer"]:
        return "integer"
    if checks["real"]:
        return "real"
    if checks["datetime"]:
        return "datetime"
    if checks["date"]:
        return "date"
    return "text"


def _pii_class(name: str, values: list[str], ctype: str = "text") -> str | None:
    for cls, rx in _PII_NAME.items():
        if rx.search(name):
            return cls
    if ctype != "text":  # only sniff values on free-text columns (a date/number is not a phone)
        return None
    sample = [v for v in values if v][:50]
    if sample and sum(bool(_EMAIL_RE.match(v)) for v in sample) / len(sample) > 0.6:
        return "email"
    if sample and sum(bool(_PHONE_RE.match(v)) for v in sample) / len(sample) > 0.6:
        return "phone"
    return None


def profile_table(name: str, rows: list[dict], kind: str = "tabular") -> dict[str, Any]:
    """Profile one table: columns (type/nulls/distinct/min/max/samples), candidate keys, PII, quality."""
    columns = _column_order(rows)
    n = len(rows)
    col_profiles: list[dict] = []
    pii: list[dict] = []
    keys: list[str] = []

    for col in columns:
        raw = [(_get(r, col)) for r in rows]
        nonnull = [v for v in raw if v is not None and str(v).strip() != ""]
        ctype = infer_column_type([str(v) for v in nonnull])
        distinct = len(set(nonnull))
        prof: dict[str, Any] = {
            "name": col,
            "type": ctype,
            "nulls": n - len(nonnull),
            "null_pct": round((n - len(nonnull)) / n, 4) if n else 0.0,
            "distinct": distinct,
            "samples": [str(v) for v in nonnull[:3]],
        }
        if ctype in ("integer", "real") and nonnull:
            nums = [float(v) for v in nonnull]
            prof["min"], prof["max"] = min(nums), max(nums)
        col_profiles.append(prof)

        # candidate primary key: unique + no nulls, and an identifier-ish type
        # (a real/boolean column that happens to be all-distinct is a measure, not a key)
        if n > 1 and distinct == n and prof["nulls"] == 0 and ctype not in ("real", "boolean"):
            keys.append(col)

        cls = _pii_class(col, [str(v) for v in nonnull], ctype)
        if cls:
            pii.append({"column": f"{name}.{col}", "class": cls, "action": "flagged"})

    quality = _quality_checks(name, n, col_profiles, keys)
    return {
        "name": name,
        "kind": kind,
        "rows": n,
        "columns": col_profiles,
        "candidate_keys": keys,
        "pii": pii,
        "quality": quality,
    }


def _quality_checks(name: str, n: int, cols: list[dict], keys: list[str]) -> list[dict]:
    checks: list[dict] = []
    checks.append({"check": f"{name} row_count > 0", "result": "pass" if n > 0 else "fail",
                   "detail": f"{n} rows"})
    high_null = [c["name"] for c in cols if c["null_pct"] > 0.5]
    checks.append({"check": f"{name} columns <50% null",
                   "result": "warn" if high_null else "pass",
                   "detail": (", ".join(high_null) + " >50% null") if high_null else "ok"})
    checks.append({"check": f"{name} has a candidate key",
                   "result": "pass" if keys else "warn",
                   "detail": (", ".join(keys)) if keys else "no unique non-null column"})
    return checks


def _column_order(rows: list[dict]) -> list[str]:
    seen: dict[str, None] = {}
    for r in rows:
        for k in r.keys():
            seen.setdefault(k, None)
    return list(seen.keys())


def _get(row: dict, col: str):
    v = row.get(col)
    return v
