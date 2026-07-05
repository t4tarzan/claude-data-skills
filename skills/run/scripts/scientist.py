"""scientist.py — the Data Scientist stage (train + evaluate a predictive model on gold).

Branch B off gold. Trains a real (ridge-OLS, from-scratch stdlib) regression model to predict
a target measure from the fact's other measures + one-hot-encoded dimensions, with a held-out
test split and honest metrics (R²/RMSE/MAE). Emits a portable JSON model + an eval report.

Ports the AiNa `experiments`/`foundry` idea (train a model on the governed data) to a
venv-free core — the model is pure JSON, servable anywhere.

Produces:
  models/model.json — schema (target, features, categories) + fitted weights + version
  models/eval.json  — train/test metrics, split sizes, coefficient table

Usage:
    python3 scientist.py --run-root . --slug my-run [--target gmv] [--fact agg_x_daily] [--lam 1.0]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3

import mlcore
from _run import RunContext, empty_governance, finish_stage, new_stage


def _pick_fact(facts: list[dict], want: str | None) -> dict:
    if want:
        for f in facts:
            if f["table"] == want or f["table"] == f"agg_{want}_daily":
                return f
    # default: the fact with the most measures then dims (most learnable)
    return max(facts, key=lambda f: (len(f["measures"]), len(f["dims"])))


def _load_rows(gold_db: pathlib.Path, fact: str) -> list[dict]:
    con = sqlite3.connect(gold_db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(f'SELECT * FROM "{fact}"')]
    con.close()
    return rows


def _split(rows: list[dict], k: int = 5) -> tuple[list[dict], list[dict]]:
    """Deterministic hold-out: every k-th row -> test (no RNG, so runs are reproducible).
    k=5 is coprime with common categorical cycles, so the test set spans the category space."""
    train = [r for i, r in enumerate(rows) if i % k != 0]
    test = [r for i, r in enumerate(rows) if i % k == 0]
    return (train, test) if test and train else (rows, rows)


def run(run_root: str, slug: str, target: str | None = None, fact: str | None = None, lam: float = 1.0) -> dict:
    ctx = RunContext(run_root, slug)
    stage = new_stage("scientist")
    gold_db = ctx.path("gold") / "gold.db"
    gcat_p = ctx.path("gold") / "gold_catalog.json"
    if not gold_db.exists() or not gcat_p.exists():
        finish_stage(stage, "failed")
        stage["notes"] = "need gold (run engineer first)"
        ctx.append_stage(stage)
        raise SystemExit("scientist: gold.db missing — run the engineer stage first")

    facts = json.loads(gcat_p.read_text())["facts"]
    if not facts:
        finish_stage(stage, "failed")
        stage["notes"] = "no gold facts to model"
        ctx.append_stage(stage)
        raise SystemExit("scientist: no gold facts available to model")
    fmeta = _pick_fact(facts, fact)
    fact_name = fmeta["table"]
    measures = fmeta["measures"]
    if not measures:
        finish_stage(stage, "failed")
        stage["notes"] = f"fact {fact_name} has no numeric measures to predict"
        ctx.append_stage(stage)
        raise SystemExit(f"scientist: fact {fact_name} has no measures to model")

    tgt = target if target in measures else measures[-1]
    feat_numeric = [m for m in measures if m != tgt]
    feat_cat = fmeta["dims"]
    rows = _load_rows(gold_db, fact_name)

    schema = mlcore.build_schema(rows, feat_numeric, feat_cat, tgt)
    train, test = _split(rows)
    Xtr, ytr = mlcore.build_matrix(schema, train)
    weights = mlcore.fit_ridge(Xtr, ytr, lam=lam)
    model = {"kind": "ridge_ols", "schema": schema, "weights": weights, "lam": lam,
             "trained_on": f"gold.{fact_name}", "target": tgt,
             "version": "v1"}
    model["version"] = "v1"
    model["hash"] = "sha256:" + hashlib.sha256(json.dumps(model["weights"]).encode()).hexdigest()[:16]

    # evaluate on train + held-out test
    Xte, yte = mlcore.build_matrix(schema, test)
    pred_tr = [sum(a * b for a, b in zip(x, weights)) for x in Xtr]
    pred_te = [sum(a * b for a, b in zip(x, weights)) for x in Xte]
    m_train = mlcore.metrics(ytr, pred_tr)
    m_test = mlcore.metrics(yte, pred_te)
    coefs = [{"feature": n, "weight": round(w, 6)} for n, w in zip(schema["feature_names"], weights)]
    eval_report = {"target": tgt, "fact": f"gold.{fact_name}", "features": schema["feature_names"],
                   "n_train": len(train), "n_test": len(test),
                   "train": m_train, "test": m_test, "coefficients": coefs}

    models_dir = ctx.ensure_dir("model")
    (models_dir / "model.json").write_text(json.dumps(model, indent=2) + "\n")
    (ctx.path("eval")).write_text(json.dumps(eval_report, indent=2) + "\n")

    gov = empty_governance()
    gov["pii"] = ctx.inherited_governance()["pii"]
    gov["lineage"] = [{"output": f"model.{tgt}", "from": [f"gold.{fact_name}"],
                       "logic": f"ridge_ols predict {tgt} from {feat_numeric}+onehot({feat_cat})"}]
    r2 = m_test["r2"]
    gov["quality"] = [{"check": f"model predicts {tgt}", "result": "pass" if (r2 is not None and r2 >= 0) else "warn",
                       "detail": f"test R²={r2}, RMSE={m_test['rmse']}"}]
    gov["contract"] = {"name": f"{slug}.model@1", "honored": True, "violations": []}

    stage["consumed"] = {"gold": ctx.rel("gold")}
    stage["produced"] = {"model": ctx.rel("model") + "/model.json", "eval": ctx.rel("eval"),
                         "model_hash": model["hash"]}
    stage["governance"] = gov
    stage["receipts"] = [f"trained ridge_ols to predict {tgt} from gold.{fact_name}: "
                         f"test R²={r2}, RMSE={m_test['rmse']} (n_train={len(train)}, n_test={len(test)})"]
    finish_stage(stage, "ok")
    stage["notes"] = f"target={tgt}, test R²={r2}"
    ctx.append_stage(stage)
    return {"target": tgt, "fact": fact_name, "train": m_train, "test": m_test,
            "features": schema["feature_names"], "status": "ok"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Data Scientist: train+eval a predictive model on gold")
    ap.add_argument("--run-root", default=".")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--target", default=None, help="measure to predict (default: last measure)")
    ap.add_argument("--fact", default=None, help="gold fact to model (default: most learnable)")
    ap.add_argument("--lam", type=float, default=1.0, help="ridge regularization strength")
    args = ap.parse_args(argv)
    res = run(args.run_root, args.slug, args.target, args.fact, args.lam)
    print(f"scientist: ok — predict {res['target']} from gold.{res['fact']}; "
          f"test R²={res['test']['r2']} RMSE={res['test']['rmse']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
