"""ml.py — the ML Engineer stage (package + version + serve the model behind an API).

Branch B, downstream of the Data Scientist. Takes the fitted JSON model and productionizes
it: a self-contained `service/` bundle (model + core + a stdlib HTTP server + manifest +
README) that any box can run with `python3 serve.py` — no venv, no framework. Ports the AiNa
`foundry`/`finetune-platform` idea (serve a model behind an API) to a venv-free artifact.

Produces:
  service/model.json  service/mlcore.py  service/serve.py  service/manifest.json  service/README.md

Usage:
    python3 ml.py --run-root . --slug my-run
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil

from _run import RunContext, empty_governance, finish_stage, new_stage

_SERVE_PY = '''#!/usr/bin/env python3
"""Self-contained model server (stdlib only). Run: python3 serve.py --port 8080
POST /predict {"features": {...}} -> {"prediction": <float>}   ·   GET /health"""
import argparse, json, pathlib, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mlcore
MODEL = json.loads((HERE / "model.json").read_text())
STATS = {"requests_total": 0, "errors_total": 0}

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "target": MODEL["target"], "version": MODEL["version"]})
        elif self.path == "/metrics":
            # Prometheus text exposition (scraped by the Data SRE ServiceMonitor)
            body = ("# TYPE dt_up gauge\\ndt_up 1\\n"
                    "# TYPE dt_requests_total counter\\ndt_requests_total %d\\n"
                    "# TYPE dt_errors_total counter\\ndt_errors_total %d\\n"
                    % (STATS["requests_total"], STATS["errors_total"])).encode()
            self.send_response(200); self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        else:
            self._send(404, {"error": "not found"})
    def do_POST(self):
        if self.path != "/predict":
            self._send(404, {"error": "not found"}); return
        STATS["requests_total"] += 1
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            feats = body.get("features", body)
            pred = mlcore.predict_one(MODEL, feats)
            self._send(200, {"prediction": pred, "target": MODEL["target"], "model_version": MODEL["version"]})
        except Exception as e:  # noqa: BLE001
            STATS["errors_total"] += 1
            self._send(400, {"error": str(e)})
    def log_message(self, *a):
        pass

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    print(f"serving model '{MODEL['target']}' {MODEL['version']} on http://{a.host}:{a.port}")
    HTTPServer((a.host, a.port), Handler).serve_forever()
'''


def _input_spec(schema: dict) -> dict:
    return {"numeric": schema["numeric"],
            "categorical": {c: (["<baseline>"] + vals) for c, vals in schema["categorical"].items()}}


def run(run_root: str, slug: str) -> dict:
    ctx = RunContext(run_root, slug)
    stage = new_stage("ml")
    model_p = ctx.path("model") / "model.json"
    if not model_p.exists():
        finish_stage(stage, "failed")
        stage["notes"] = "no model (run scientist first)"
        ctx.append_stage(stage)
        raise SystemExit("ml: model.json missing — run the scientist stage first")
    model = json.loads(model_p.read_text())

    svc = ctx.ensure_dir("service")
    shutil.copy2(model_p, svc / "model.json")
    shutil.copy2(pathlib.Path(__file__).resolve().parent / "mlcore.py", svc / "mlcore.py")
    (svc / "serve.py").write_text(_SERVE_PY)

    manifest = {
        "service": f"{slug}-model", "kind": "prediction-api", "model_version": model["version"],
        "model_hash": model["hash"], "target": model["target"], "trained_on": model["trained_on"],
        "endpoints": {"POST /predict": "{'features': {...}} -> {'prediction': float}",
                      "GET /health": "-> {status, target, version}"},
        "input_spec": _input_spec(model["schema"]),
        "run": "python3 serve.py --port 8080", "runtime": "python3 stdlib only (no venv)",
    }
    (svc / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (svc / "README.md").write_text(
        f"# {slug}-model — prediction service\n\n"
        f"Predicts **{model['target']}** (ridge OLS, {model['version']}), trained on `{model['trained_on']}`.\n\n"
        f"```bash\npython3 serve.py --port 8080\n"
        f"curl -s localhost:8080/health\n"
        f"curl -s -X POST localhost:8080/predict -d '{{\"features\": {{...}}}}'\n```\n\n"
        f"Inputs: numeric {model['schema']['numeric']}; "
        f"categorical {list(model['schema']['categorical'].keys())}. Stdlib only, no venv.\n")

    gov = empty_governance()
    gov["pii"] = ctx.inherited_governance()["pii"]
    gov["lineage"] = [{"output": f"service.{slug}-model", "from": [f"model.{model['target']}"],
                       "logic": f"package {model['version']} ({model['hash']}) as a stdlib prediction API"}]
    gov["quality"] = [{"check": "service self-contained", "result": "pass",
                       "detail": "model.json + mlcore.py + serve.py + manifest"}]
    gov["contract"] = {"name": f"{slug}.service@1", "honored": True, "violations": []}

    stage["consumed"] = {"model": ctx.rel("model") + "/model.json"}
    stage["produced"] = {"service": ctx.rel("service")}
    stage["governance"] = gov
    stage["receipts"] = [f"packaged {model['target']} model {model['version']} ({model['hash']}) as a "
                         f"self-contained stdlib prediction API (POST /predict)"]
    finish_stage(stage, "ok")
    stage["notes"] = f"service {slug}-model {model['version']}"
    ctx.append_stage(stage)
    return {"service": str(svc), "target": model["target"], "version": model["version"], "status": "ok"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ML Engineer: package + serve the model behind an API")
    ap.add_argument("--run-root", default=".")
    ap.add_argument("--slug", required=True)
    args = ap.parse_args(argv)
    res = run(args.run_root, args.slug)
    print(f"ml: ok — service for '{res['target']}' {res['version']} -> {res['service']} (python3 serve.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
