"""engineer.py — the Data Engineer stage (medallion: bronze -> silver -> gold + lineage).

Reads the Architect's bronze (bronze.db + catalog.json) and builds:
  silver/silver.db  — cleaned, type-conformed, de-duplicated tables (safe universal cleaning)
  gold/gold.db      — modeled facts (agg_<t>_daily) + conformed dimensions (dim_<t>), indexed
  lineage/engineer.json — the bronze->silver->gold graph

Ports the AiNa medallion *concepts* from metrics-mart/martdb (gold = agg_<domain>_daily
facts with governed lineage; "gold matches the raw fact" reconciliation) to a stdlib-only
SQLite engine — consistent with the Architect (system python has no duckdb/pandas).

Every summed gold measure is reconciled against silver (the signature receipt). Set-based
SQL throughout; window functions for dedupe; a Python UDF only for date normalization.

Usage:
    python3 engineer.py --run-root . --slug my-run
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sqlite3

from _run import RunContext, empty_governance, file_hash, finish_stage, new_stage

_DATE_FMTS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y", "%Y%m%d",
              "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ")


def _norm_date(v):
    """Date-normalization UDF: any recognized date/datetime string -> ISO 'YYYY-MM-DD'; else unchanged."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    for fmt in _DATE_FMTS:
        try:
            return _dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _clean_expr(col: dict) -> str:
    """SQL expression that cleans one column for silver, by its bronze type."""
    c = _q(col["name"])
    t = col["type"]
    if t == "text":
        return f"nullif(trim({c}), '')"
    if t in ("date", "datetime"):
        return f"norm_date({c})"
    return c  # integer / real / boolean already coerced at bronze


def _build_silver(con: sqlite3.Connection, catalog: dict) -> tuple[list[dict], list[dict]]:
    """Create silver tables (clean + type-conform + dedupe). Returns (lineage, quality)."""
    lineage, quality = [], []
    for t in catalog["tables"]:
        if t["kind"] != "tabular":
            continue
        name = t["table"].split(".", 1)[1]
        cols = t["columns"]
        keys = t.get("candidate_keys") or []
        select_cols = ", ".join(f"{_clean_expr(c)} AS {_q(c['name'])}" for c in cols)
        partition = ", ".join(_q(k) for k in keys) if keys else ", ".join(_q(c["name"]) for c in cols)
        # keep the first row per key (or first of each exact-duplicate group)
        sql = (f'CREATE TABLE silver.{_q(name)} AS '
               f'SELECT {select_cols} FROM ('
               f'  SELECT *, ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY rowid) AS _rn '
               f'  FROM bronze.{_q(name)}) WHERE _rn = 1')
        con.execute(f'DROP TABLE IF EXISTS silver.{_q(name)}')
        con.execute(sql)
        bronze_n = con.execute(f'SELECT count(*) FROM bronze.{_q(name)}').fetchone()[0]
        silver_n = con.execute(f'SELECT count(*) FROM silver.{_q(name)}').fetchone()[0]
        dropped = bronze_n - silver_n
        lineage.append({"output": f"silver.{name}", "from": [f"bronze.{name}"],
                        "logic": f"clean(trim/null/date-normalize) + dedupe on {keys or 'all columns'}"
                                 f" ({dropped} dup row(s) dropped)"})
        quality.append({"check": f"silver.{name} dedupe", "result": "pass",
                        "detail": f"{bronze_n}->{silver_n} rows ({dropped} dropped)"})
    return lineage, quality


def _classify(t: dict) -> dict:
    """Split a table's columns into grain (date), measures (numeric non-key), dims (low-card non-pii-key)."""
    keys = set(t.get("candidate_keys") or [])
    pii = {p["column"].split(".", 1)[1] for p in _table_pii(t)}
    grain = next((c["name"] for c in t["columns"] if c["type"] in ("date", "datetime")), None)
    measures = [c["name"] for c in t["columns"]
                if c["type"] in ("integer", "real") and c["name"] not in keys and c["name"] != grain]
    dims = [c["name"] for c in t["columns"]
            if c["type"] in ("text", "boolean") and c["name"] not in keys
            and c["name"] not in pii and c["name"] != grain
            and 1 < c["distinct"] <= max(50, t["rows"] // 2 or 50)]
    return {"grain": grain, "measures": measures, "dims": dims, "keys": sorted(keys), "pii": sorted(pii)}


def _table_pii(t: dict) -> list[dict]:
    # PII lives in the stage manifest, but the catalog carries enough via column names;
    # we re-derive from the catalog's own dimensions exclusion is insufficient, so read flags if present.
    return t.get("_pii", [])


def _build_gold(con: sqlite3.Connection, catalog: dict, pii_by_table: dict) -> tuple[list, list, list]:
    """Create gold facts (agg_<t>_daily) + dims (dim_<t>). Returns (lineage, quality, receipts)."""
    lineage, quality, receipts = [], [], []
    for t in catalog["tables"]:
        if t["kind"] != "tabular":
            continue
        t = {**t, "_pii": pii_by_table.get(t["table"].split(".", 1)[1], [])}
        name = t["table"].split(".", 1)[1]
        c = _classify(t)

        if c["grain"] and c["measures"]:
            gold_name = f"agg_{name}_daily"
            grain_day = f"{c['grain']}_day"
            dim_sel = ", ".join(_q(d) for d in c["dims"])
            meas_sel = ", ".join(f"sum({_q(m)}) AS {_q(m)}" for m in c["measures"])
            group_cols = f"date({_q(c['grain'])})" + (", " + dim_sel if c["dims"] else "")
            select = (f"date({_q(c['grain'])}) AS {_q(grain_day)}"
                      + (", " + dim_sel if c["dims"] else "")
                      + f", {meas_sel}, count(*) AS {_q(name + '_count')}")
            con.execute(f'DROP TABLE IF EXISTS gold.{_q(gold_name)}')
            con.execute(f'CREATE TABLE gold.{_q(gold_name)} AS '
                        f'SELECT {select} FROM silver.{_q(name)} GROUP BY {group_cols}')
            # index the grain + dims (SQLite; columnar via DuckDB when available)
            idx_cols = ", ".join([_q(grain_day)] + [_q(d) for d in c["dims"]])
            con.execute(f'CREATE INDEX gold.{_q("ix_" + gold_name)} ON {_q(gold_name)} ({idx_cols})')
            lineage.append({"output": f"gold.{gold_name}", "from": [f"silver.{name}"],
                            "logic": f"agg by day({c['grain']}) x {c['dims']}: sum({c['measures']}), count"})
            # reconciliation: every summed measure must match silver (the signature receipt)
            for m in c["measures"]:
                s = con.execute(f'SELECT round(coalesce(sum({_q(m)}),0),6) FROM silver.{_q(name)}').fetchone()[0]
                g = con.execute(f'SELECT round(coalesce(sum({_q(m)}),0),6) FROM gold.{_q(gold_name)}').fetchone()[0]
                ok = (s == g)
                quality.append({"check": f"gold.{gold_name} reconciles {m}",
                                "result": "pass" if ok else "fail",
                                "detail": f"silver Σ={s} gold Σ={g}"})
                receipts.append(f"gold.{gold_name}.{m} matches raw fact (Σ={g})"
                                if ok else f"RECONCILE FAIL {gold_name}.{m}: {s} != {g}")
        else:
            # reference/dimension table: conform (drop PII columns), already deduped in silver
            dim_name = f"dim_{name}"
            keep = [col["name"] for col in t["columns"] if col["name"] not in c["pii"]]
            sel = ", ".join(_q(k) for k in keep) if keep else "*"
            con.execute(f'DROP TABLE IF EXISTS gold.{_q(dim_name)}')
            con.execute(f'CREATE TABLE gold.{_q(dim_name)} AS SELECT {sel} FROM silver.{_q(name)}')
            dropped_pii = c["pii"]
            lineage.append({"output": f"gold.{dim_name}", "from": [f"silver.{name}"],
                            "logic": f"conform dimension (keys {c['keys']})"
                                     + (f"; dropped PII {dropped_pii}" if dropped_pii else "")})
            n = con.execute(f'SELECT count(*) FROM gold.{_q(dim_name)}').fetchone()[0]
            quality.append({"check": f"gold.{dim_name} conformed", "result": "pass",
                            "detail": f"{n} rows; PII dropped: {dropped_pii or 'none'}"})
    return lineage, quality, receipts


def run(run_root: str, slug: str) -> dict:
    ctx = RunContext(run_root, slug)
    stage = new_stage("engineer")
    bronze_db = ctx.path("bronze") / "bronze.db"
    catalog_p = ctx.path("catalog")
    if not bronze_db.exists() or not catalog_p.exists():
        finish_stage(stage, "failed")
        stage["notes"] = "no bronze (run architect first)"
        ctx.append_stage(stage)
        raise SystemExit("engineer: bronze.db / catalog.json missing — run the architect stage first")
    catalog = json.loads(catalog_p.read_text())

    # PII per table from the architect's stage manifest (authoritative source of flags)
    pii_by_table: dict[str, list] = {}
    for s in ctx.load().get("stages", []):
        if s.get("stage") == "architect":
            for p in s.get("governance", {}).get("pii", []):
                tbl, col = p["column"].split(".", 1)
                pii_by_table.setdefault(tbl, []).append({"column": p["column"]})

    silver_db = ctx.ensure_dir("silver") / "silver.db"
    gold_db = ctx.ensure_dir("gold") / "gold.db"
    for p in (silver_db, gold_db):
        if p.exists():
            p.unlink()

    # --- silver: attach bronze, build cleaned/deduped tables ---
    scon = sqlite3.connect(silver_db)
    scon.create_function("norm_date", 1, _norm_date)
    scon.execute("ATTACH DATABASE ? AS bronze", (str(bronze_db),))
    scon.execute("ATTACH DATABASE ? AS silver", (str(silver_db),))
    sil_lineage, sil_quality = _build_silver(scon, catalog)
    scon.commit()
    scon.close()

    # --- gold: attach silver, build facts + dims, reconcile ---
    gcon = sqlite3.connect(gold_db)
    gcon.execute("ATTACH DATABASE ? AS silver", (str(silver_db),))
    gcon.execute("ATTACH DATABASE ? AS gold", (str(gold_db),))
    gold_lineage, gold_quality, receipts = _build_gold(gcon, catalog, pii_by_table)
    gcon.commit()
    gcon.close()

    # --- lineage graph (inherit architect's file->bronze edges) + write file ---
    inherited = ctx.inherited_governance()
    full_lineage = inherited["lineage"] + sil_lineage + gold_lineage
    lineage_p = ctx.ensure_dir("lineage") / "engineer.json"
    lineage_p.write_text(json.dumps({"edges": full_lineage}, indent=2) + "\n")

    # --- governance envelope (extends, never restarts) ---
    gov = empty_governance()
    gov["lineage"] = sil_lineage + gold_lineage
    gov["pii"] = inherited["pii"]  # PII carried forward; dropped-in-gold noted in lineage logic
    gov["quality"] = sil_quality + gold_quality
    reconcile_fail = any(q["result"] == "fail" for q in gold_quality)
    gov["contract"] = {"name": f"{slug}.gold@1", "honored": not reconcile_fail,
                       "violations": [q["check"] for q in gold_quality if q["result"] == "fail"]}

    stage["consumed"] = {"bronze": ctx.rel("bronze")}
    stage["produced"] = {"silver": ctx.rel("silver"), "gold": ctx.rel("gold"),
                         "lineage": ctx.rel("lineage") + "/engineer.json",
                         "silver_db_hash": file_hash(silver_db), "gold_db_hash": file_hash(gold_db)}
    stage["governance"] = gov
    facts = [l["output"] for l in gold_lineage if l["output"].split(".")[1].startswith("agg_")]
    dims = [l["output"] for l in gold_lineage if l["output"].split(".")[1].startswith("dim_")]
    stage["receipts"] = receipts + [f"gold: {len(facts)} fact(s) + {len(dims)} dim(s)"]
    status = "failed" if reconcile_fail else "ok"
    finish_stage(stage, status)
    stage["notes"] = f"{len(facts)} facts, {len(dims)} dims; {'RECONCILE FAILED' if reconcile_fail else 'all reconciled'}"
    ctx.append_stage(stage)
    return {"facts": facts, "dims": dims, "receipts": receipts, "status": status,
            "silver_db": str(silver_db), "gold_db": str(gold_db)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Data Engineer: medallion bronze -> silver -> gold")
    ap.add_argument("--run-root", default=".")
    ap.add_argument("--slug", required=True)
    args = ap.parse_args(argv)
    res = run(args.run_root, args.slug)
    print(f"engineer: {res['status']} — facts={[f.split('.')[1] for f in res['facts']]} "
          f"dims={[d.split('.')[1] for d in res['dims']]}")
    for r in res["receipts"]:
        print("  ·", r)
    return 0 if res["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
