"""architect.py — the Data Architect stage (raw sources -> typed bronze + catalog).

Ingest & catalog raw sources; profile types/keys/quality; land them as typed tables in a
bronze SQLite database, and emit a source catalog + governance envelope. This is the head
of the spine (docs/00-role-stage-catalog.md).

Bronze is a single SQLite DB (`bronze/bronze.db`) — stdlib, typed, queryable, universal —
plus `bronze/catalog.json` (the profile). AiNa starts at silver/gold and treats raw as
out of scope, so this raw->bronze step is net-new; it reuses AiNa's *catalog shape*
(owner/grain/dimensions/freshness/row-counts).

Usage:
    python3 architect.py --run-root . --slug my-run SOURCE [SOURCE ...]
    python3 architect.py --run-root . --slug my-run ./data_folder
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import sqlite3
import sys

import adapters
import profile as prof
from _run import RunContext, empty_governance, file_hash, finish_stage, new_stage

_SQL_TYPE = {"integer": "INTEGER", "real": "REAL", "boolean": "INTEGER",
             "date": "TEXT", "datetime": "TEXT", "text": "TEXT"}


def _ident(stem: str) -> str:
    """Sanitize a filename stem into a safe SQL table identifier."""
    out = "".join(c if c.isalnum() else "_" for c in stem).strip("_").lower()
    if not out or out[0].isdigit():
        out = "t_" + out
    return out


def _collect(sources: list[str]) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for s in sources:
        p = pathlib.Path(s)
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*") if f.suffix.lower() in adapters.SUPPORTED_EXT))
        elif p.is_file():
            files.append(p)
    return files


def _coerce(value: str, ctype: str):
    """Convert a raw string into the inferred type for SQLite storage; '' -> NULL."""
    if value is None or str(value).strip() == "":
        return None
    v = str(value).strip()
    try:
        if ctype == "integer":
            return int(float(v)) if _looks_float(v) else int(v)
        if ctype == "real":
            return float(v)
        if ctype == "boolean":
            return 1 if v.lower() in {"true", "yes", "y", "t", "1"} else 0
    except (ValueError, TypeError):
        return v
    return v


def _looks_float(v: str) -> bool:
    return "." in v or "e" in v.lower()


def run(run_root: str, slug: str, sources: list[str], copy_sources: bool = True) -> dict:
    ctx = RunContext(run_root, slug)
    stage = new_stage("architect")
    files = _collect(sources)
    if not files:
        finish_stage(stage, "failed")
        stage["notes"] = "no supported source files found"
        ctx.append_stage(stage)
        raise SystemExit("architect: no supported source files found in " + ", ".join(sources))

    bronze_dir = ctx.ensure_dir("bronze")
    db_path = bronze_dir / "bronze.db"
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)

    catalog: dict = {"layer": "bronze", "generated_by": "data-team:architect", "tables": []}
    governance = empty_governance()
    skipped: list[dict] = []
    used_names: set[str] = set()

    if copy_sources:
        src_dir = ctx.ensure_dir("sources")

    for f in files:
        try:
            rows, meta = adapters.ingest(f)
        except adapters.AdapterError as e:
            skipped.append({"source": f.name, "reason": str(e)})
            continue

        name = _ident(f.stem)
        while name in used_names:
            name += "_x"
        used_names.add(name)

        tprof = prof.profile_table(name, rows, kind=meta.get("kind", "tabular"))
        _write_table(con, name, tprof, rows)
        pii_cols = {p["column"].split(".", 1)[1] for p in tprof["pii"]}

        stat = f.stat()
        freshness = __import__("datetime").datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        catalog["tables"].append({
            "table": f"bronze.{name}",
            "source_file": f.name,
            "kind": tprof["kind"],
            "rows": tprof["rows"],
            "columns": tprof["columns"],
            "candidate_keys": tprof["candidate_keys"],
            "dimensions": [c["name"] for c in tprof["columns"]
                           if c["type"] in ("text", "boolean")
                           and 1 < c["distinct"] <= max(50, tprof["rows"] // 4 or 50)
                           and c["name"] not in tprof["candidate_keys"]
                           and c["name"] not in pii_cols],
            "freshness": freshness,
            "owner": "unassigned",  # a human/downstream assigns the CDM owner
        })
        # governance: lineage seed (bronze table <- source file) + pii + quality
        governance["lineage"].append({"output": f"bronze.{name}", "from": [f"file:{f.name}"],
                                      "logic": f"ingest {meta.get('kind','tabular')} via architect"})
        governance["pii"].extend(tprof["pii"])
        governance["quality"].extend(tprof["quality"])

        if copy_sources:
            shutil.copy2(f, src_dir / f.name)

    con.commit()
    con.close()

    catalog["table_count"] = len(catalog["tables"])
    catalog["skipped"] = skipped
    (ctx.path("catalog")).write_text(__import__("json").dumps(catalog, indent=2, ensure_ascii=False) + "\n")

    # finalize manifest
    stage["consumed"] = {"sources": [str(f) for f in files]}
    stage["produced"] = {"bronze": ctx.rel("bronze"), "catalog": ctx.rel("catalog"),
                         "bronze_db": ctx.rel("bronze") + "/bronze.db", "bronze_db_hash": file_hash(db_path)}
    governance["contract"] = {"name": f"{slug}.bronze@1", "honored": True, "violations": []}
    stage["governance"] = governance
    stage["receipts"] = [f"landed {catalog['table_count']} table(s), "
                         f"{sum(t['rows'] for t in catalog['tables'])} rows into bronze.db"] + \
                        ([f"skipped {len(skipped)}: " + "; ".join(s['reason'] for s in skipped)] if skipped else [])
    status = "partial" if skipped else "ok"
    finish_stage(stage, status)
    stage["notes"] = f"{catalog['table_count']} tables; {len(governance['pii'])} PII column(s) flagged"
    ctx.append_stage(stage)
    return {"tables": catalog["table_count"], "rows": sum(t["rows"] for t in catalog["tables"]),
            "skipped": skipped, "db": str(db_path), "catalog": str(ctx.path("catalog")),
            "pii": len(governance["pii"]), "status": status}


def _write_table(con: sqlite3.Connection, name: str, tprof: dict, rows: list[dict]) -> None:
    cols = tprof["columns"]
    col_defs = ", ".join(f'"{c["name"]}" {_SQL_TYPE.get(c["type"], "TEXT")}' for c in cols)
    con.execute(f'DROP TABLE IF EXISTS "{name}"')
    con.execute(f'CREATE TABLE "{name}" ({col_defs})')
    colnames = [c["name"] for c in cols]
    ctypes = {c["name"]: c["type"] for c in cols}
    placeholders = ", ".join("?" for _ in colnames)
    quoted = ", ".join(f'"{c}"' for c in colnames)
    batch = [[_coerce(r.get(c), ctypes[c]) for c in colnames] for r in rows]
    con.executemany(f'INSERT INTO "{name}" ({quoted}) VALUES ({placeholders})', batch)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Data Architect: ingest & catalog raw sources -> bronze")
    ap.add_argument("--run-root", default=".", help="root under which data-team-out/<slug>/ lives")
    ap.add_argument("--slug", required=True, help="run slug")
    ap.add_argument("--no-copy-sources", action="store_true", help="do not copy raw files into sources/")
    ap.add_argument("sources", nargs="+", help="source files or directories")
    args = ap.parse_args(argv)
    res = run(args.run_root, args.slug, args.sources, copy_sources=not args.no_copy_sources)
    print(f"architect: {res['status']} — {res['tables']} table(s), {res['rows']} rows, "
          f"{res['pii']} PII flagged -> {res['db']}")
    if res["skipped"]:
        for s in res["skipped"]:
            print(f"  skipped {s['source']}: {s['reason']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
