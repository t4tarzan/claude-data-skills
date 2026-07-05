"""analyst.py — the Data Analyst stage (management questions -> reports + charts).

Turns natural-language questions into governed answers over gold + the semantic layer, each
with a RECEIPT (metric definition, owner, version, the exact SQL). A deterministic NL->plan
planner (the nlq "fast lane") derives its vocabulary from the semantic layer itself — metric
ids/labels and the gold dimension dictionary — instead of hardcoding, so it generalizes to
any dataset. Every answer honors the governance guard (ratios recompute; distinct-counts are
never summed across grains — reused from dsl.py).

Produces:
  reports/report.md   — human narrative + tables, one section per question
  reports/report.json — machine answers (value(s), plan, SQL, receipt)
  visuals/*.svg       — a bar chart per breakdown question (stdlib SVG, no matplotlib)

If no questions are supplied, a few are auto-generated from the semantic layer so a spine
run always yields a report.

Usage:
    python3 analyst.py --run-root . --slug my-run [--ask "..."] [--ask "..."]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sqlite3

import dsl
from _run import RunContext, empty_governance, finish_stage, new_stage


# ---- planning (deterministic NL -> query plan) ------------------------------

_BREAKDOWN_TRIGGERS = ("by ", "per ", "across ", "for each ", "grouped by ", "breakdown", "break down")
_RANK_TRIGGERS = ("top ", "best ", "highest ", "most ")
_BOTTOM_TRIGGERS = ("bottom ", "worst ", "lowest ", "least ")


def _match_metric(q: str, metrics: list[dict]) -> dict | None:
    """Longest metric id/label substring present in the question wins."""
    ql = q.lower()
    best, best_len = None, 0
    for m in metrics:
        for cand in (m["metric"], m.get("label", "")):
            c = cand.lower().strip()
            if c and c in ql and len(c) > best_len:
                best, best_len = m, len(c)
    return best


def _match_breakdown(q: str, dims: list[str]) -> str | None:
    ql = q.lower()
    if not any(t in ql for t in _BREAKDOWN_TRIGGERS + _RANK_TRIGGERS + _BOTTOM_TRIGGERS):
        return None
    # longest dimension name mentioned
    hit, hlen = None, 0
    for d in dims:
        if d.lower() in ql and len(d) > hlen:
            hit, hlen = d, len(d)
    return hit


def _match_filters(q: str, dim_values: dict[str, list[str]]) -> list[tuple[str, str]]:
    ql = q.lower()
    out: list[tuple[str, str]] = []
    for dim, values in dim_values.items():
        for v in values:
            if v and str(v).lower() in ql:
                out.append((dim, v))
                break
    return out


def _match_limit(q: str) -> int | None:
    m = re.search(r"top\s+(\d+)|bottom\s+(\d+)", q.lower())
    if m:
        return int(m.group(1) or m.group(2))
    return None


def plan_question(q: str, metrics: list[dict], dims: list[str], dim_values: dict) -> dict | None:
    metric = _match_metric(q, metrics)
    if not metric:
        return None
    breakdown = _match_breakdown(q, dims)
    order = "desc"
    if any(t in q.lower() for t in _BOTTOM_TRIGGERS):
        order = "asc"
    return {"question": q, "metric": metric["metric"], "breakdown": breakdown,
            "filters": _match_filters(q, dim_values), "order": order,
            "limit": _match_limit(q) or (10 if breakdown else None)}


# ---- compilation (plan -> SQL, guard-honoring) ------------------------------

def compile_sql(plan: dict, metric: dict) -> tuple[str, dict]:
    """Compile a plan to SQL over the metric's rollup fact. Ratios recompute via dsl.golden_query;
    the guard note records the aggregation class so a non-additive rollup is never silent."""
    fact = (metric.get("rollups") or {}).get("daily", "").split(".", 1)[-1]
    expr = dsl.golden_query(metric)
    mid = metric["metric"]
    select = (f'"{plan["breakdown"]}", ' if plan["breakdown"] else "") + f'{expr} AS "{mid}"'
    where = ""
    params: list = []
    if plan["filters"]:
        clauses = []
        for dim, val in plan["filters"]:
            clauses.append(f'"{dim}" = ?')
            params.append(val)
        where = " WHERE " + " AND ".join(clauses)
    group = f' GROUP BY "{plan["breakdown"]}"' if plan["breakdown"] else ""
    order = f' ORDER BY "{mid}" {plan["order"].upper()}' if plan["breakdown"] else ""
    limit = f' LIMIT {plan["limit"]}' if (plan["breakdown"] and plan["limit"]) else ""
    sql = f'SELECT {select} FROM "{fact}"{where}{group}{order}{limit}'
    guard = dsl.reaggregation_verdict(metric["aggregation"], "daily", "daily")  # same-grain here
    return sql, {"params": params, "class": dsl.aggregation_class(metric["aggregation"]),
                 "guard": guard, "recomputed": metric["aggregation"] in dsl.RATIO_LIKE}


# ---- charts (stdlib SVG, no deps) -------------------------------------------

def svg_bar(title: str, pairs: list[tuple[str, float]], unit: str = "") -> str:
    if not pairs:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="60"><text x="10" y="30">{_esc(title)}: no data</text></svg>'
    w, rowh, pad, labelw = 480, 26, 34, 150
    h = pad + rowh * len(pairs) + 12
    mx = max(abs(v) for _, v in pairs) or 1
    barw = w - labelw - 70
    rows = []
    for i, (lab, val) in enumerate(pairs):
        y = pad + i * rowh
        bl = max(2, int(barw * abs(val) / mx))
        rows.append(
            f'<text x="8" y="{y+15}" font-size="12" fill="#2A2833">{_esc(str(lab))[:22]}</text>'
            f'<rect x="{labelw}" y="{y+3}" width="{bl}" height="16" fill="#6B53A3" rx="3"/>'
            f'<text x="{labelw+bl+6}" y="{y+15}" font-size="11" fill="#4A3A7A">{_fmt(val)}{_esc(unit)}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" font-family="Inter,system-ui,sans-serif">'
            f'<text x="8" y="20" font-size="14" font-weight="700" fill="#4A3A7A">{_esc(title)}</text>'
            + "".join(rows) + "</svg>")


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.0f}"
    return f"{v:,}"


# ---- the stage --------------------------------------------------------------

def _dim_values(gold_db: pathlib.Path, facts: list[dict]) -> dict[str, list[str]]:
    con = sqlite3.connect(gold_db)
    vals: dict[str, list[str]] = {}
    for f in facts:
        for d in f.get("dims", []):
            try:
                rows = con.execute(f'SELECT DISTINCT "{d}" FROM "{f["table"]}" WHERE "{d}" IS NOT NULL LIMIT 100').fetchall()
                vals.setdefault(d, [])
                vals[d].extend(str(r[0]) for r in rows)
            except sqlite3.Error:
                pass
    con.close()
    return {k: sorted(set(v)) for k, v in vals.items()}


def _auto_questions(metrics: list[dict], dims: list[str]) -> list[str]:
    qs: list[str] = []
    numeric = [m for m in metrics if m["aggregation"] in (dsl.ADDITIVE | dsl.RATIO_LIKE)]
    for m in numeric[:3]:
        qs.append(f"What is total {m.get('label', m['metric'])}?")
        if dims:
            qs.append(f"{m.get('label', m['metric'])} by {dims[0]}")
    return qs


def run(run_root: str, slug: str, questions: list[str] | None = None) -> dict:
    ctx = RunContext(run_root, slug)
    stage = new_stage("analyst")
    gold_db = ctx.path("gold") / "gold.db"
    sem_p = ctx.path("semantic") / "semantic.json"
    if not gold_db.exists() or not sem_p.exists():
        finish_stage(stage, "failed")
        stage["notes"] = "need gold + semantic (run engineer + designer first)"
        ctx.append_stage(stage)
        raise SystemExit("analyst: gold.db / semantic.json missing — run engineer + designer first")

    semantic = json.loads(sem_p.read_text())
    metrics = semantic["metrics"]
    by_id = {m["metric"]: m for m in metrics}
    gold_cat = json.loads((ctx.path("gold") / "gold_catalog.json").read_text())
    facts = gold_cat["facts"]
    dims = sorted(semantic["dimensions"].keys())
    dim_values = _dim_values(gold_db, facts)

    questions = questions or _auto_questions(metrics, dims)
    reports_dir = ctx.ensure_dir("reports")
    visuals_dir = ctx.ensure_dir("visuals")

    con = sqlite3.connect(gold_db)
    answers: list[dict] = []
    md = [f"# Analyst report — {slug}\n", f"_{len(questions)} question(s) over the governed semantic layer._\n"]
    quality, lineage = [], []
    for i, q in enumerate(questions):
        plan = plan_question(q, metrics, dims, dim_values)
        if not plan:
            md.append(f"## {q}\n\n> No metric matched this question.\n")
            answers.append({"question": q, "answered": False})
            quality.append({"check": f"Q{i+1} answered", "result": "warn", "detail": "no metric match"})
            continue
        metric = by_id[plan["metric"]]
        sql, meta = compile_sql(plan, metric)
        cur = con.execute(sql, meta["params"])
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        receipt = {"metric": metric["metric"], "definition": metric.get("description", ""),
                   "owner": metric.get("owner"), "version": metric.get("version"),
                   "aggregation": metric["aggregation"], "class": meta["class"],
                   "lineage": metric.get("lineage", []), "sql": sql,
                   "note": "ratio recomputed from components" if meta["recomputed"] else "additive"}
        answers.append({"question": q, "answered": True, "plan": plan, "columns": cols,
                        "rows": [list(r) for r in rows], "receipt": receipt})
        lineage.append({"output": f"report.q{i+1}", "from": [f"metric.{metric['metric']}"],
                        "logic": f"{metric['aggregation']} over gold; {receipt['note']}"})
        quality.append({"check": f"Q{i+1} answered", "result": "pass", "detail": metric["metric"]})

        # narrative + table
        md.append(f"## {q}\n")
        md.append(f"**Metric:** `{metric['metric']}` ({metric['aggregation']}, {metric.get('owner')}, "
                  f"{metric.get('version')}) — {metric.get('description','')}\n")
        if plan["breakdown"]:
            md.append(f"| {plan['breakdown']} | {metric['metric']} |\n|---|---|")
            for r in rows:
                md.append(f"| {r[0]} | {_fmt(r[1])} |")
            md.append("")
            # chart
            pairs = [(str(r[0]), float(r[1]) if r[1] is not None else 0.0) for r in rows]
            svg = svg_bar(f"{metric.get('label', metric['metric'])} by {plan['breakdown']}",
                          pairs, unit=" " + metric.get("unit", "") if metric.get("unit") else "")
            svg_name = f"q{i+1}_{metric['metric']}_by_{plan['breakdown']}.svg"
            (visuals_dir / svg_name).write_text(svg)
            md.append(f"![chart](../visuals/{svg_name})\n")
            answers[-1]["chart"] = f"visuals/{svg_name}"
        else:
            val = rows[0][0] if rows and rows[0] else None
            md.append(f"**Answer:** {_fmt(val) if val is not None else 'n/a'} {metric.get('unit','')}\n")
        md.append(f"<sub>receipt · lineage {receipt['lineage']} · `{sql}`</sub>\n")
    con.close()

    (reports_dir / "report.md").write_text("\n".join(md) + "\n")
    (reports_dir / "report.json").write_text(json.dumps({"slug": slug, "answers": answers}, indent=2) + "\n")

    gov = empty_governance()
    gov["lineage"] = lineage
    gov["pii"] = ctx.inherited_governance()["pii"]
    gov["quality"] = quality
    answered = sum(1 for a in answers if a.get("answered"))
    gov["contract"] = {"name": f"{slug}.reports@1", "honored": True, "violations": []}
    charts = sum(1 for a in answers if a.get("chart"))
    stage["consumed"] = {"gold": ctx.rel("gold"), "semantic": ctx.rel("semantic")}
    stage["produced"] = {"reports": ctx.rel("reports"), "visuals": ctx.rel("visuals")}
    stage["governance"] = gov
    stage["receipts"] = [f"{answered}/{len(questions)} question(s) answered; {charts} chart(s); every answer carries a receipt"]
    finish_stage(stage, "ok" if answered else "partial")
    stage["notes"] = f"{answered} answered, {charts} charts"
    ctx.append_stage(stage)
    return {"answered": answered, "questions": len(questions), "charts": charts,
            "report": str(reports_dir / "report.md"), "status": stage["status"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Data Analyst: questions -> governed reports + charts")
    ap.add_argument("--run-root", default=".")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--ask", action="append", default=None, help="a question (repeatable)")
    args = ap.parse_args(argv)
    res = run(args.run_root, args.slug, args.ask)
    print(f"analyst: {res['status']} — {res['answered']}/{res['questions']} answered, "
          f"{res['charts']} chart(s) -> {res['report']}")
    return 0 if res["answered"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
