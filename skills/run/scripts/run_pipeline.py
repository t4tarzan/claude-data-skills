"""run_pipeline.py — the orchestrator: resolve a stage selection into a coherent DAG and run it.

Implements docs/02-selection-resolver.md. Given a stage selection (any subset, any order),
any bring-your-own artifacts, and a missing-input policy, it resolves the dependency graph
(auto-synthesizing missing upstream stages under the default policy), writes the plan into
manifest.json, and executes the stages in topological (spine) order.

v1 wires the spine (architect->engineer->designer->analyst). Branch/plane stages (bi,
scientist, ml, sre) are registered in the contract so the resolver already reasons about
them; they raise NotImplemented until their phases land.

Usage:
    python3 run_pipeline.py --slug my-run --sources ./data [--stages analyst] \\
        [--metrics-dir ./metrics] [--ask "gmv by platform"] [--policy synthesize] [--gold ./wh]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil

import analyst
import architect
import bi as bi_stage
import designer
import engineer
from _run import RunContext

# the stage contract (docs/01) — consumes/produces per stage. `optional` never forces a producer.
STAGES: dict[str, dict] = {
    "architect": {"consumes": ["sources"], "produces": ["bronze", "catalog"]},
    "engineer":  {"consumes": ["bronze"], "produces": ["silver", "gold", "lineage"]},
    "designer":  {"consumes": ["gold"], "optional": [], "produces": ["semantic", "conformance"]},
    "analyst":   {"consumes": ["gold"], "optional": ["semantic"], "produces": ["reports", "visuals"]},
    # branches + plane (registered for the resolver; implemented in P6-P8)
    "bi":        {"consumes": ["gold", "semantic"], "produces": ["dashboard", "access_model"]},
    "scientist": {"consumes": ["gold"], "produces": ["model", "eval"], "todo": True},
    "ml":        {"consumes": ["model"], "produces": ["service"], "todo": True},
    "sre":       {"consumes": ["service"], "produces": ["deployment"], "todo": True},
}
SPINE_ORDER = ["architect", "engineer", "designer", "analyst", "bi", "scientist", "ml", "sre"]
PRODUCER = {k: s for s, spec in STAGES.items() for k in spec["produces"]}
PRODUCER["sources"] = None  # user-supplied raw input, not produced by a stage


class ResolveError(Exception):
    pass


def resolve(selection: list[str], supplied: set[str], policy: str) -> tuple[list[str], list[str]]:
    """Return (ordered stages to run, human reasons). Honors supplied artifacts + missing-input policy."""
    required: set[str] = set()
    reasons: list[str] = []

    def need(stage: str):
        if stage not in STAGES:
            raise ResolveError(f"unknown stage: {stage!r}")
        if stage in required:
            return
        required.add(stage)
        for key in STAGES[stage]["consumes"]:
            if key == "sources" or key in supplied:
                continue
            prod = PRODUCER.get(key)
            if prod is None:
                raise ResolveError(f"stage {stage!r} needs {key!r} — supply it (--{key}) [no producer]")
            if prod not in selection and prod not in required:
                if policy == "strict":
                    raise ResolveError(
                        f"stage {stage!r} needs {key!r}; supply --{key} <path> or add {prod!r} to --stages")
                reasons.append(f"{stage} needs {key} → added {prod}")
            need(prod)

    for s in selection:
        need(s)
    order = [s for s in SPINE_ORDER if s in required]
    todo = [s for s in order if STAGES[s].get("todo")]
    if todo:
        raise ResolveError(f"stage(s) not implemented yet in this version: {', '.join(todo)} (spine = "
                           "architect,engineer,designer,analyst)")
    return order, reasons


def _place_supplied(ctx: RunContext, supplied_paths: dict[str, str]) -> set[str]:
    """Copy bring-your-own artifacts into the run dir so downstream stages find them. Returns keys satisfied."""
    keys: set[str] = set()
    for key, src in supplied_paths.items():
        dst = ctx.ensure_dir(key)
        srcp = pathlib.Path(src)
        if srcp.is_dir():
            shutil.copytree(srcp, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(srcp, dst)
        keys.add(key)
    return keys


def _run_stage(stage: str, opts: dict) -> dict:
    root, slug = opts["run_root"], opts["slug"]
    if stage == "architect":
        return architect.run(root, slug, opts["sources"], copy_sources=opts.get("copy_sources", True))
    if stage == "engineer":
        return engineer.run(root, slug)
    if stage == "designer":
        return designer.run(root, slug, metrics_dir=opts.get("metrics_dir"))
    if stage == "analyst":
        return analyst.run(root, slug, questions=opts.get("ask"))
    if stage == "bi":
        return bi_stage.run(root, slug, cadence=opts.get("refresh_cadence", "daily"))
    raise NotImplementedError(stage)


def run(run_root: str, slug: str, selection: list[str], opts: dict) -> dict:
    ctx = RunContext(run_root, slug)
    supplied_paths = {k: v for k, v in opts.get("supplied", {}).items() if v}
    supplied_keys = _place_supplied(ctx, supplied_paths) if supplied_paths else set()

    order, reasons = resolve(selection, supplied_keys, opts.get("policy", "synthesize"))
    if "architect" in order and not opts.get("sources"):
        raise ResolveError("architect is in the plan but no --sources given (supply raw files, "
                           "or supply --gold and select only downstream stages)")

    ctx.set_plan({"selection": selection, "supplied": sorted(supplied_keys),
                  "policy": opts.get("policy", "synthesize"), "resolved": order,
                  "reason": "; ".join(reasons) or "selection needs no synthesis",
                  "engines": opts.get("engines", {})})

    results = {}
    for stage in order:
        opts2 = {**opts, "run_root": run_root, "slug": slug}
        results[stage] = _run_stage(stage, opts2)
    return {"plan": order, "reasons": reasons, "results": results,
            "run_dir": str(ctx.dir), "manifest": str(ctx.manifest_path)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="data-team pipeline runner (resolve selection -> run the DAG)")
    ap.add_argument("--run-root", default=".")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--stages", default="architect,engineer,designer,analyst",
                    help="comma-separated subset (default: the full spine)")
    ap.add_argument("--sources", nargs="*", default=None, help="raw source files/dirs (for architect)")
    ap.add_argument("--metrics-dir", default=None, help="authored metric defs (for designer)")
    ap.add_argument("--ask", action="append", default=None, help="a question (for analyst; repeatable)")
    ap.add_argument("--gold", default=None, help="bring-your-own gold dir (skip architect+engineer)")
    ap.add_argument("--policy", default="synthesize", choices=["synthesize", "ask", "strict"])
    args = ap.parse_args(argv)

    selection = [s.strip() for s in args.stages.split(",") if s.strip()]
    opts = {"run_root": args.run_root, "slug": args.slug, "sources": args.sources,
            "metrics_dir": args.metrics_dir, "ask": args.ask,
            "policy": "synthesize" if args.policy == "ask" else args.policy,
            "supplied": {"gold": args.gold}}
    try:
        res = run(args.run_root, args.slug, selection, opts)
    except ResolveError as e:
        print(f"resolve error: {e}")
        return 2
    print(f"pipeline: ran {', '.join(res['plan'])}")
    for r in res["reasons"]:
        print(f"  · {r}")
    print(f"  -> {res['run_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
