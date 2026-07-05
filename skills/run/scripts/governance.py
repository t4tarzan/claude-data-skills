"""governance.py — the cross-cutting governance plane: aggregate every stage's envelope.

Governance is not a stage; it is a block on every stage manifest (lineage · pii · contract ·
quality) that propagates forward. This module walks the finished run's manifest and produces
one consolidated view: the full lineage graph, a PII register with each column's final
disposition, contract adherence, a quality/SLA scoreboard, the access policy (from BI RBAC),
and SLOs (from the SRE plane) — plus an overall governed/not-governed verdict.

Produces:
  governance/report.json — structured aggregate
  governance/report.html — self-contained readable report

Usage:
    python3 governance.py --run-root . --slug my-run
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib

from _run import RunContext


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _pii_disposition(col: str, lineage_logics: list[str]) -> str:
    """Best-effort final disposition of a PII column from lineage notes (dropped in gold vs carried)."""
    short = col.split(".", 1)[-1]
    for lg in lineage_logics:
        if "dropped PII" in lg and short in lg:
            return "dropped in gold"
    return "flagged — carried forward"


def build_report(ctx: RunContext, slug: str) -> dict:
    manifest = ctx.load()
    stages = manifest.get("stages", [])
    stage_names = [s["stage"] for s in stages]

    # --- lineage: union of all edges (file -> bronze -> ... -> deployment) ---
    edges: list[dict] = []
    logics: list[str] = []
    for s in stages:
        for e in s.get("governance", {}).get("lineage", []):
            edges.append(e)
            logics.append(e.get("logic", ""))
    outputs = sorted({e["output"] for e in edges})
    froms = {f for e in edges for f in e.get("from", [])}
    roots = sorted(froms - set(outputs))

    # --- PII register: dedupe columns, record class + first stage + disposition ---
    seen: dict[str, dict] = {}
    for s in stages:
        for p in s.get("governance", {}).get("pii", []):
            key = p["column"]
            if key not in seen:
                seen[key] = {"column": key, "class": p.get("class", "?"), "first_stage": s["stage"],
                             "disposition": _pii_disposition(key, logics)}
    pii = sorted(seen.values(), key=lambda x: x["column"])

    # --- contracts: one per stage ---
    contracts = [{"stage": s["stage"], **s.get("governance", {}).get("contract", {})} for s in stages]
    honored = all(c.get("honored", True) for c in contracts)

    # --- quality scoreboard + reconciliation + conformance ---
    by_stage: dict[str, dict] = {}
    totals = {"pass": 0, "warn": 0, "fail": 0}
    for s in stages:
        counts = {"pass": 0, "warn": 0, "fail": 0}
        for q in s.get("governance", {}).get("quality", []):
            r = q.get("result", "pass")
            counts[r] = counts.get(r, 0) + 1
            totals[r] = totals.get(r, 0) + 1
        by_stage[s["stage"]] = counts
    reconciliation = [r for s in stages if s["stage"] == "engineer" for r in s.get("receipts", [])
                      if "matches raw fact" in r or "RECONCILE" in r]

    conformance = None
    conf_p = ctx.path("conformance")
    if conf_p.exists():
        c = json.loads(conf_p.read_text())["metrics"]
        conformance = {"total": len(c), "pass": sum(1 for x in c if x.get("result") == "pass"),
                       "non_additive_guarded": [x["metric"] for x in c if x.get("rollup_guard", {}).get("ok") is False]}

    # --- access policy (BI RBAC) + SLOs (SRE) if present ---
    access_policy = None
    am = ctx.path("access_model")
    if am.exists():
        a = json.loads(am.read_text())
        access_policy = {"model": a.get("model"), "roles": list(a.get("roles", {}).keys()),
                         "restricted_owners": a.get("restricted_owners", [])}
    slos = None
    dj = ctx.path("deployment") / "deployment.json"
    if dj.exists():
        d = json.loads(dj.read_text())
        slos = [{"name": x["name"], "slo": x.get("slo"), "observability": x.get("observability")}
                for x in d.get("deployables", [])]

    issues: list[str] = []
    for c in contracts:
        if not c.get("honored", True):
            issues.append(f"contract {c.get('name')} violated: {c.get('violations')}")
    if totals["fail"]:
        issues.append(f"{totals['fail']} quality check(s) failed")

    return {
        "slug": slug, "generated_at": _now(), "stages": stage_names,
        "lineage": {"edges": edges, "roots": roots, "outputs": outputs, "edge_count": len(edges)},
        "pii": {"columns": pii, "count": len(pii)},
        "contracts": contracts, "all_contracts_honored": honored,
        "quality": {"by_stage": by_stage, "totals": totals,
                    "reconciliation": reconciliation, "conformance": conformance},
        "access_policy": access_policy, "slos": slos,
        "verdict": {"governed": honored and totals["fail"] == 0, "issues": issues},
    }


def _html(rep: dict) -> str:
    v = rep["verdict"]
    badge = ("#2e7d32", "GOVERNED") if v["governed"] else ("#c62828", "ISSUES")
    rows_pii = "".join(f"<tr><td><code>{_e(p['column'])}</code></td><td>{_e(p['class'])}</td>"
                       f"<td>{_e(p['first_stage'])}</td><td>{_e(p['disposition'])}</td></tr>" for p in rep["pii"]["columns"]) \
        or '<tr><td colspan="4">no PII flagged</td></tr>'
    rows_c = "".join(f"<tr><td>{_e(c['stage'])}</td><td><code>{_e(c.get('name',''))}</code></td>"
                     f"<td>{'✅' if c.get('honored', True) else '❌ '+_e(str(c.get('violations')))}</td></tr>"
                     for c in rep["contracts"])
    t = rep["quality"]["totals"]
    rows_q = "".join(f"<tr><td>{_e(s)}</td><td>{c['pass']}</td><td>{c['warn']}</td><td>{c['fail']}</td></tr>"
                     for s, c in rep["quality"]["by_stage"].items())
    recon = "".join(f"<li>{_e(r)}</li>" for r in rep["quality"]["reconciliation"]) or "<li>n/a</li>"
    lin = "".join(f"<li><code>{_e(e['output'])}</code> ← {_e(', '.join(e.get('from', [])))} "
                  f"<span class='muted'>{_e(e.get('logic',''))}</span></li>" for e in rep["lineage"]["edges"])
    acc = rep["access_policy"]
    acc_html = (f"<p>Model: {_e(acc['model'])} · roles: {_e(', '.join(acc['roles']))} · "
                f"restricted owners: {_e(', '.join(acc['restricted_owners']) or 'none')}</p>") if acc else "<p>n/a (no BI stage)</p>"
    slo_html = ("".join(f"<li><code>{_e(s['name'])}</code>: {_e(json.dumps(s['slo']))} "
                        f"· observability {'on' if s['observability'] else 'off'}</li>" for s in rep["slos"])
                if rep["slos"] else "<li>n/a (no SRE stage)</li>")
    conf = rep["quality"]["conformance"]
    conf_html = (f"<p>{conf['pass']}/{conf['total']} metrics conform · rollup-guarded: "
                 f"{_e(', '.join(conf['non_additive_guarded']) or 'none')}</p>") if conf else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{_e(rep['slug'])} — governance</title>
<style>body{{font-family:Inter,system-ui,sans-serif;margin:0;background:#FAF9FC;color:#2A2833}}
header{{background:#4A3A7A;color:#fff;padding:16px 24px}} .wrap{{padding:20px 24px;max-width:960px}}
h2{{color:#4A3A7A;border-bottom:1px solid #E7E4EE;padding-bottom:4px;margin-top:26px}}
table{{width:100%;border-collapse:collapse;font-size:13px}} td,th{{border:1px solid #E7E4EE;padding:6px 9px;text-align:left}}
th{{background:#F7F5FB;color:#4A3A7A}} code{{background:#F7F5FB;border:1px solid #D9D1EC;border-radius:4px;padding:0 4px}}
.badge{{display:inline-block;color:#fff;border-radius:6px;padding:2px 10px;font-weight:700;background:{badge[0]}}}
.muted{{color:#6E6E76;font-size:11px}} ul{{line-height:1.6}}</style></head><body>
<header><h1 style="margin:0;font-size:18px">{_e(rep['slug'])} — governance report <span class="badge">{badge[1]}</span></h1>
<div style="opacity:.8;font-size:12px">generated {_e(rep['generated_at'])} · stages: {_e(', '.join(rep['stages']))}</div></header>
<div class="wrap">
<h2>Verdict</h2><p>{'All contracts honored, no failed checks.' if v['governed'] else 'Issues: ' + _e('; '.join(v['issues']))}</p>
<h2>PII register ({rep['pii']['count']})</h2><table><tr><th>column</th><th>class</th><th>first flagged</th><th>disposition</th></tr>{rows_pii}</table>
<h2>Contracts</h2><table><tr><th>stage</th><th>contract</th><th>honored</th></tr>{rows_c}</table>
<h2>Quality scoreboard (pass/warn/fail: {t['pass']}/{t['warn']}/{t['fail']})</h2>
<table><tr><th>stage</th><th>pass</th><th>warn</th><th>fail</th></tr>{rows_q}</table>
<p><b>Reconciliation:</b></p><ul>{recon}</ul>{conf_html}
<h2>Access policy</h2>{acc_html}
<h2>SLOs</h2><ul>{slo_html}</ul>
<h2>Lineage ({rep['lineage']['edge_count']} edges)</h2><ul>{lin}</ul>
</div></body></html>"""


def _e(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def run(run_root: str, slug: str) -> dict:
    ctx = RunContext(run_root, slug)
    if not ctx.manifest_path.exists():
        raise SystemExit("governance: no run manifest — run the pipeline first")
    rep = build_report(ctx, slug)
    gdir = ctx.dir / "governance"
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "report.json").write_text(json.dumps(rep, indent=2, default=str) + "\n")
    (gdir / "report.html").write_text(_html(rep))
    # record a lightweight pointer in the manifest (governance is a plane, not a stage)
    m = ctx.load()
    m["governance_report"] = {"path": "governance/report.json", "verdict": rep["verdict"],
                              "pii_columns": rep["pii"]["count"], "generated_at": rep["generated_at"]}
    ctx.save(m)
    return {"verdict": rep["verdict"], "pii": rep["pii"]["count"], "edges": rep["lineage"]["edge_count"],
            "quality": rep["quality"]["totals"], "report": str(gdir / "report.html"), "status": "ok"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Governance plane: aggregate every stage's envelope into one report")
    ap.add_argument("--run-root", default=".")
    ap.add_argument("--slug", required=True)
    args = ap.parse_args(argv)
    res = run(args.run_root, args.slug)
    v = res["verdict"]
    print(f"governance: {'GOVERNED' if v['governed'] else 'ISSUES'} — {res['pii']} PII column(s), "
          f"{res['edges']} lineage edges, quality {res['quality']} -> {res['report']}")
    if v["issues"]:
        for i in v["issues"]:
            print("  ·", i)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
