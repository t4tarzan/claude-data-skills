"""designer.py — the Data Designer stage (governed semantic layer / metric DSL over gold).

Builds the semantic layer: metric + dimension definitions over the gold facts, versioned,
each checked by the governance guard (dsl.reaggregation_verdict) and compiled against gold
(a real dry-run, not a mock — AiNa's "TRUTH gate"). Emits:
  semantic/metrics/<id>.json  — one governed metric definition each
  semantic/semantic.json      — the metric dictionary + dimension dictionary + version store
  semantic/conformance.json    — per-metric: compiles? aggregation class? guard verdict?

The DETERMINISTIC core auto-proposes the safe *additive* metrics from the gold schema (a
sum per measure, an orders-style count). Ratios / distinct-counts / derived metrics are the
Data Designer's authored IP — drop them as JSON in a --metrics-dir and they are versioned
+ conformance-checked here, so a ratio can never be silently summed up a grain.

Usage:
    python3 designer.py --run-root . --slug my-run [--metrics-dir ./my_metrics]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3

import dsl
from _run import RunContext, empty_governance, finish_stage, new_stage


def _gold_facts(ctx: RunContext) -> dict[str, dict]:
    """Read the gold contract the Engineer declared (grain/dims/measures per fact) — no guessing."""
    cat_p = ctx.path("gold") / "gold_catalog.json"
    if not cat_p.exists():
        return {}
    cat = json.loads(cat_p.read_text())
    facts: dict[str, dict] = {}
    for f in cat.get("facts", []):
        facts[f["table"]] = {"grain": f["grain"], "dims": f["dims"], "measures": f["measures"],
                             "count_col": f["count_col"], "types": f.get("measure_types", {}),
                             "owner": f.get("owner", "unassigned")}
    return facts


def _propose(facts: dict[str, dict]) -> list[dict]:
    """Auto-propose the safe additive metrics from gold (sum per measure + a count metric)."""
    out: list[dict] = []
    for fact, meta in facts.items():
        owner = meta.get("owner", "unassigned")
        base_dims = meta["dims"]
        for m in meta["measures"]:
            is_int = meta["types"].get(m) == "integer"
            out.append({"metric": m, "label": m.replace("_", " ").title(),
                        "description": f"Sum of {m} over the {fact} fact.",
                        "aggregation": "sum", "measure": m,
                        "dimensions": base_dims, "time_grains": ["daily", "weekly", "monthly"],
                        "rollups": {"daily": f"gold.{fact}"}, "owner": owner,
                        "unit": "count" if is_int else "", "round": 0 if is_int else 2,
                        "lineage": [f"gold.{fact}"], "_proposed": True})
        if meta["count_col"]:
            base = meta["count_col"][:-len("_count")] or "rows"
            out.append({"metric": base, "label": base.replace("_", " ").title(),
                        "description": f"Count of {base} (additive rollup of {meta['count_col']}).",
                        "aggregation": "sum", "measure": meta["count_col"],
                        "dimensions": base_dims, "time_grains": ["daily", "weekly", "monthly"],
                        "rollups": {"daily": f"gold.{fact}"}, "owner": owner,
                        "unit": "count", "round": 0, "lineage": [f"gold.{fact}"], "_proposed": True})
    return out


def _version_store(prev: dict, metric: dict) -> dict:
    """Assign/bump version by content hash vs the previous semantic store. Returns the metric w/ version."""
    mid = metric["metric"]
    body = {k: v for k, v in metric.items() if not k.startswith("_") and k not in ("version", "_hash")}
    h = "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    old = prev.get(mid)
    if old is None:
        metric["version"] = metric.get("version", "v1")
        metric["changelog"] = [{"version": metric["version"], "change": "created"}]
    elif old.get("_hash") == h:
        metric["version"] = old.get("version", "v1")
        metric["changelog"] = old.get("changelog", [])
    else:
        n = int(str(old.get("version", "v1")).lstrip("v") or 1) + 1
        metric["version"] = f"v{n}"
        metric["changelog"] = old.get("changelog", []) + [{"version": f"v{n}", "change": "definition changed"}]
    metric["_hash"] = h
    return metric


def _conformance(gold_db: pathlib.Path, metric: dict) -> dict:
    """Compile-check the golden query against gold (real dry-run) + the governance guard verdict."""
    issues = dsl.validate_metric(metric)
    entry = {"metric": metric["metric"], "aggregation": metric["aggregation"],
             "class": dsl.aggregation_class(metric["aggregation"]), "issues": issues}
    fact = (metric.get("rollups") or {}).get("daily", "").split(".", 1)[-1]
    if issues:
        entry["compiles"] = False
        entry["result"] = "fail"
        return entry
    try:
        gq = dsl.golden_query(metric)
        con = sqlite3.connect(gold_db)
        con.execute(f'SELECT {gq} FROM "{fact}"').fetchone()  # dry-run
        con.close()
        entry["compiles"] = True
        entry["golden_query"] = gq
    except (sqlite3.Error, ValueError) as e:
        entry["compiles"] = False
        entry["result"] = "fail"
        entry["error"] = str(e)[:120]
        return entry
    # governance guard: can this roll daily -> monthly by summing?
    v = dsl.reaggregation_verdict(metric["aggregation"], "daily", "monthly")
    entry["rollup_guard"] = v
    entry["result"] = "pass"  # compiles; guard is informational (non-additive is not an error, it's a rule)
    return entry


def run(run_root: str, slug: str, metrics_dir: str | None = None) -> dict:
    ctx = RunContext(run_root, slug)
    stage = new_stage("designer")
    gold_db = ctx.path("gold") / "gold.db"
    if not gold_db.exists():
        finish_stage(stage, "failed")
        stage["notes"] = "no gold (run engineer first)"
        ctx.append_stage(stage)
        raise SystemExit("designer: gold.db missing — run the engineer stage first")

    facts = _gold_facts(ctx)

    # previous semantic store (for versioning across re-runs)
    sem_path = ctx.ensure_dir("semantic")
    prev_file = sem_path / "semantic.json"
    prev = {m["metric"]: m for m in json.loads(prev_file.read_text())["metrics"]} if prev_file.exists() else {}

    metrics = _propose(facts)
    authored = 0
    if metrics_dir:
        for f in sorted(pathlib.Path(metrics_dir).glob("*.json")) + sorted(pathlib.Path(metrics_dir).glob("*.y*ml")):
            m = dsl.load_metric_file(f)
            m["_proposed"] = False
            metrics = [x for x in metrics if x["metric"] != m["metric"]] + [m]  # authored overrides proposed
            authored += 1

    # version + conformance
    conformance = []
    metrics_out = []
    metrics_dir_out = sem_path / "metrics"
    metrics_dir_out.mkdir(exist_ok=True)
    lineage = []
    quality = []
    for m in sorted(metrics, key=lambda x: x["metric"]):
        m = _version_store(prev, m)
        conf = _conformance(gold_db, m)
        conformance.append(conf)
        metrics_out.append(m)
        (metrics_dir_out / f"{m['metric']}.json").write_text(json.dumps(m, indent=2) + "\n")
        src = (m.get("rollups") or {}).get("daily", "")
        lineage.append({"output": f"metric.{m['metric']}", "from": [src] if src else [],
                        "logic": f"{m['aggregation']} ({dsl.aggregation_class(m['aggregation'])}) v{m['version'].lstrip('v')}"})
        quality.append({"check": f"metric {m['metric']} conforms",
                        "result": conf["result"], "detail": conf.get("error", conf.get("golden_query", ""))})

    dim_dictionary = _dimension_dictionary(facts)
    semantic = {"layer": "semantic", "metrics": metrics_out, "dimensions": dim_dictionary,
                "metric_count": len(metrics_out), "authored": authored,
                "proposed": len(metrics_out) - authored}
    prev_file.write_text(json.dumps(semantic, indent=2, ensure_ascii=False) + "\n")
    (sem_path / "conformance.json").write_text(json.dumps({"metrics": conformance}, indent=2) + "\n")

    # governance: guard verdicts summarized; a non-additive metric flagged (rule, not failure)
    gov = empty_governance()
    gov["lineage"] = lineage
    gov["pii"] = ctx.inherited_governance()["pii"]
    gov["quality"] = quality
    failures = [c["metric"] for c in conformance if c["result"] == "fail"]
    non_additive = [c["metric"] for c in conformance if c.get("rollup_guard", {}).get("ok") is False]
    gov["contract"] = {"name": f"{slug}.semantic@1", "honored": not failures, "violations": failures}

    stage["consumed"] = {"gold": ctx.rel("gold")}
    stage["produced"] = {"semantic": ctx.rel("semantic"), "conformance": ctx.rel("conformance")}
    stage["governance"] = gov
    stage["receipts"] = [f"{len(metrics_out)} metric(s) governed ({semantic['proposed']} proposed, {authored} authored); "
                         f"{len(conformance) - len(failures)}/{len(conformance)} conform"] + \
                        ([f"guard: non-additive, rollup-protected: {', '.join(non_additive)}"] if non_additive else [])
    status = "failed" if failures else "ok"
    finish_stage(stage, status)
    stage["notes"] = f"{len(metrics_out)} metrics; {len(non_additive)} rollup-guarded; {len(failures)} failing"
    ctx.append_stage(stage)
    return {"metrics": len(metrics_out), "authored": authored, "conform": len(conformance) - len(failures),
            "failures": failures, "non_additive": non_additive, "status": status}


def _dimension_dictionary(facts: dict[str, dict]) -> dict[str, list[str]]:
    dims: dict[str, set] = {}
    for meta in facts.values():
        for d in meta["dims"]:
            dims.setdefault(d, set())
    return {k: sorted(v) for k, v in sorted(dims.items())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Data Designer: governed semantic layer / metric DSL over gold")
    ap.add_argument("--run-root", default=".")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--metrics-dir", default=None, help="dir of authored metric JSON/YAML defs (ratios, distinct-counts)")
    args = ap.parse_args(argv)
    res = run(args.run_root, args.slug, args.metrics_dir)
    print(f"designer: {res['status']} — {res['metrics']} metric(s), {res['conform']} conform, "
          f"{len(res['non_additive'])} rollup-guarded")
    if res["non_additive"]:
        print("  guard (non-additive, protected from naive rollup):", ", ".join(res["non_additive"]))
    if res["failures"]:
        print("  FAILED conformance:", ", ".join(res["failures"]))
    return 0 if res["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
