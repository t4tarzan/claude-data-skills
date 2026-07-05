"""bi.py — the BI Engineer stage (refreshing, role-scoped BI off gold).

Branch A off gold. Builds a dashboard from the governed semantic layer: a KPI panel per
metric + a breakdown panel per metric x primary dimension, each materialized from gold
(guard-honoring SQL via dsl). Emits a role-based access model (RBAC at the edge, AiNa's
X-Principal model — roles: admin/analyst/leadership/viewer) and a self-contained static
dashboard (inline SVG, no CDN). Re-running the stage IS the refresh.

Produces:
  bi/dashboard.json    — panels (spec + materialized data) + refresh block
  bi/access_model.json — role -> which metrics/dims/panels are visible
  bi/dashboard.html    — self-contained served dashboard (admin view; role badges per panel)

Usage:
    python3 bi.py --run-root . --slug my-run [--refresh-cadence daily]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sqlite3

import analyst as A
import dsl
from _run import RunContext, empty_governance, finish_stage, new_stage

# RBAC roles (AiNa vocabulary). Each: which metrics, dims, and panel kinds are visible.
ROLES = {
    "admin":      {"metrics": "*", "dims": "*", "panels": ["kpi", "breakdown"]},
    "analyst":    {"metrics": "*", "dims": "*", "panels": ["kpi", "breakdown"]},
    "leadership": {"metrics": "*", "dims": [], "panels": ["kpi"]},
    "viewer":     {"metrics": "public", "dims": [], "panels": ["kpi"]},
}
# metrics owned by these CDMs are restricted from the public `viewer` role
_RESTRICTED_OWNERS = {"finance-cdm", "payments-cdm"}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fact_of(metric: dict) -> str:
    return (metric.get("rollups") or {}).get("daily", "").split(".", 1)[-1]


def _visible_to(metric: dict, kind: str, dim: str | None) -> list[str]:
    restricted = metric.get("owner") in _RESTRICTED_OWNERS
    out = []
    for role, spec in ROLES.items():
        if kind not in spec["panels"]:
            continue
        if spec["metrics"] == "public" and restricted:
            continue
        if kind == "breakdown" and spec["dims"] != "*" and dim not in (spec["dims"] or []):
            continue
        out.append(role)
    return out


def _build_panels(con: sqlite3.Connection, metrics: list[dict], facts: list[dict]) -> list[dict]:
    dims_by_fact = {f["table"]: f["dims"] for f in facts}
    panels: list[dict] = []
    pid = 0
    for m in sorted(metrics, key=lambda x: x["metric"]):
        fact = _fact_of(m)
        if not fact:
            continue
        expr = dsl.golden_query(m)
        # KPI total
        pid += 1
        val = con.execute(f'SELECT {expr} FROM "{fact}"').fetchone()[0]
        panels.append({"id": f"p{pid}", "kind": "kpi", "title": m.get("label", m["metric"]),
                       "metric": m["metric"], "unit": m.get("unit", ""), "value": val,
                       "sql": f'SELECT {expr} FROM "{fact}"',
                       "visible_to": _visible_to(m, "kpi", None)})
        # one breakdown by the fact's primary dimension
        dims = dims_by_fact.get(fact, [])
        if dims:
            dim = dims[0]
            pid += 1
            sql = f'SELECT "{dim}", {expr} AS v FROM "{fact}" GROUP BY "{dim}" ORDER BY v DESC LIMIT 10'
            rows = con.execute(sql).fetchall()
            panels.append({"id": f"p{pid}", "kind": "breakdown", "title": f"{m.get('label', m['metric'])} by {dim}",
                           "metric": m["metric"], "dim": dim, "unit": m.get("unit", ""),
                           "rows": [[r[0], r[1]] for r in rows], "sql": sql,
                           "visible_to": _visible_to(m, "breakdown", dim)})
    return panels


def _dashboard_html(slug: str, panels: list[dict], refresh: dict) -> str:
    kpis = [p for p in panels if p["kind"] == "kpi"]
    brks = [p for p in panels if p["kind"] == "breakdown"]
    cards = "".join(
        f'<div class="kpi"><div class="v">{A._fmt(p["value"]) if p["value"] is not None else "n/a"}'
        f'<span class="u">{_esc(p["unit"])}</span></div><div class="t">{_esc(p["title"])}</div>'
        f'<div class="r">{_badges(p["visible_to"])}</div></div>' for p in kpis)
    charts = ""
    for p in brks:
        pairs = [(str(r[0]), float(r[1]) if r[1] is not None else 0.0) for r in p["rows"]]
        svg = A.svg_bar(p["title"], pairs, unit=(" " + p["unit"]) if p["unit"] else "")
        charts += f'<div class="panel">{svg}<div class="r">{_badges(p["visible_to"])}</div></div>'
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{_esc(slug)} — dashboard</title>
<style>
 body{{font-family:Inter,system-ui,sans-serif;margin:0;background:#FAF9FC;color:#2A2833}}
 header{{background:#4A3A7A;color:#fff;padding:16px 24px}} header h1{{margin:0;font-size:18px}}
 header .sub{{opacity:.8;font-size:12px}} .wrap{{padding:20px 24px;max-width:1000px}}
 .kpis{{display:flex;flex-wrap:wrap;gap:14px;margin-bottom:22px}}
 .kpi{{background:#fff;border:1px solid #E7E4EE;border-radius:12px;padding:14px 18px;min-width:150px}}
 .kpi .v{{font-size:24px;font-weight:800;color:#4A3A7A}} .kpi .v .u{{font-size:12px;font-weight:600;color:#6E6E76;margin-left:4px}}
 .kpi .t{{font-size:12px;color:#6E6E76;margin-top:4px}} .panel{{background:#fff;border:1px solid #E7E4EE;border-radius:12px;padding:14px;margin-bottom:14px}}
 .r{{margin-top:8px}} .b{{display:inline-block;font-size:10px;background:#F7F5FB;border:1px solid #D9D1EC;color:#4A3A7A;border-radius:5px;padding:1px 6px;margin-right:4px}}
</style></head><body>
<header><h1>{_esc(slug)} — BI dashboard</h1><div class="sub">refreshed {_esc(refresh['last_refreshed'])} · cadence {_esc(refresh['cadence'])} · RBAC at the edge (X-Principal)</div></header>
<div class="wrap"><div class="kpis">{cards}</div>{charts}</div></body></html>"""


def _badges(roles: list[str]) -> str:
    return "".join(f'<span class="b">{_esc(r)}</span>' for r in roles)


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def run(run_root: str, slug: str, cadence: str = "daily") -> dict:
    ctx = RunContext(run_root, slug)
    stage = new_stage("bi")
    gold_db = ctx.path("gold") / "gold.db"
    sem_p = ctx.path("semantic") / "semantic.json"
    if not gold_db.exists() or not sem_p.exists():
        finish_stage(stage, "failed")
        stage["notes"] = "need gold + semantic (run engineer + designer first)"
        ctx.append_stage(stage)
        raise SystemExit("bi: gold.db / semantic.json missing — run engineer + designer first")

    semantic = json.loads(sem_p.read_text())
    facts = json.loads((ctx.path("gold") / "gold_catalog.json").read_text())["facts"]
    con = sqlite3.connect(gold_db)
    panels = _build_panels(con, semantic["metrics"], facts)
    con.close()

    refresh = {"cadence": cadence, "last_refreshed": _now(), "materialized": True,
               "source": [f"gold.{f['table']}" for f in facts],
               "note": "re-run the bi stage to refresh (materializes from current gold)"}
    access_model = {"model": "RBAC at the edge (X-Principal)", "roles": ROLES,
                    "restricted_owners": sorted(_RESTRICTED_OWNERS),
                    "panel_visibility": {p["id"]: p["visible_to"] for p in panels}}

    bi_dir = ctx.ensure_dir("dashboard")
    (bi_dir / "dashboard.json").write_text(json.dumps(
        {"slug": slug, "panels": panels, "refresh": refresh}, indent=2, default=str) + "\n")
    (bi_dir / "access_model.json").write_text(json.dumps(access_model, indent=2) + "\n")
    (bi_dir / "dashboard.html").write_text(_dashboard_html(slug, panels, refresh))

    gov = empty_governance()
    gov["pii"] = ctx.inherited_governance()["pii"]
    gov["lineage"] = [{"output": f"panel.{p['id']}", "from": [f"metric.{p['metric']}"],
                       "logic": f"{p['kind']} panel; visible_to {p['visible_to']}"} for p in panels]
    gov["quality"] = [{"check": "dashboard built", "result": "pass",
                       "detail": f"{len(panels)} panels, {len(ROLES)} roles"}]
    gov["contract"] = {"name": f"{slug}.bi@1", "honored": True, "violations": []}

    stage["consumed"] = {"gold": ctx.rel("gold"), "semantic": ctx.rel("semantic")}
    stage["produced"] = {"dashboard": ctx.rel("dashboard"), "access_model": ctx.rel("access_model")}
    stage["governance"] = gov
    kpis = sum(1 for p in panels if p["kind"] == "kpi")
    stage["receipts"] = [f"{len(panels)} panels ({kpis} KPI + {len(panels)-kpis} breakdown); "
                         f"RBAC: {len(ROLES)} roles; viewer restricted from {sorted(_RESTRICTED_OWNERS)}"]
    finish_stage(stage, "ok")
    stage["notes"] = f"{len(panels)} panels; refresh cadence {cadence}"
    ctx.append_stage(stage)
    return {"panels": len(panels), "kpis": kpis, "roles": list(ROLES), "status": "ok",
            "dashboard": str(bi_dir / "dashboard.html")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="BI Engineer: refreshing, role-scoped dashboard off gold")
    ap.add_argument("--run-root", default=".")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--refresh-cadence", default="daily")
    args = ap.parse_args(argv)
    res = run(args.run_root, args.slug, args.refresh_cadence)
    print(f"bi: {res['status']} — {res['panels']} panels ({res['kpis']} KPI), roles={res['roles']} -> {res['dashboard']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
