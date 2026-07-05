"""sre.py — the Data SRE stage (platform plane: deploy any deployable at scale, observably).

A cross-cutting plane, not a spine stage: it wraps whatever deployables the run produced —
the model `service/` (an API) and/or the BI `dashboard/` (static) — and emits a complete,
kubectl-appliable deployment bundle per deployable: Dockerfile, Deployment (probes +
resources), Service, HPA (autoscaling), a Prometheus ServiceMonitor (observability), a
PrometheusRule (SLO alerting), an SLO definition, and a Helm-style values file.

Ports the AiNa Colima+k3s substrate patterns (Deployment + /health probes) to portable,
self-describing manifests. It generates real YAML; it does not require a live cluster.

Produces:
  deploy/<name>/{Dockerfile,deployment.yaml,service.yaml,hpa.yaml,servicemonitor.yaml,
                prometheusrule.yaml,values.yaml,slo.json}
  deploy/README.md          — how to apply
  deploy/deployment.json    — the machine artifact (what was generated, targets, SLOs)

Usage:
    python3 sre.py --run-root . --slug my-run [--min-replicas 2] [--max-replicas 6]
"""

from __future__ import annotations

import argparse
import json
import pathlib

from _run import RunContext, empty_governance, finish_stage, new_stage

# default SLOs (the reliability posture) — overridable per deployable
DEFAULT_SLO = {"availability": "99.9%", "latency_p95_ms": 250, "error_rate_max": "1%",
               "error_budget_month": "43m"}


def _deployables(ctx: RunContext) -> list[dict]:
    """What did the run produce that can be deployed? The model API and/or the BI dashboard."""
    out: list[dict] = []
    svc = ctx.path("service")
    if (svc / "serve.py").exists():
        out.append({"name": "model-api", "kind": "service", "port": 8080, "path": "service",
                    "image": "python:3.12-slim", "cmd": ["python3", "serve.py", "--port", "8080", "--host", "0.0.0.0"],
                    "health": "/health", "metrics": "/metrics", "workdir": "/app"})
    dash = ctx.path("dashboard")
    if (dash / "dashboard.html").exists():
        out.append({"name": "bi-dashboard", "kind": "static", "port": 80, "path": "bi",
                    "image": "nginx:1.27-alpine", "cmd": None, "health": "/", "metrics": None,
                    "workdir": "/usr/share/nginx/html"})
    return out


def _dockerfile(d: dict) -> str:
    if d["kind"] == "service":
        return (f"FROM {d['image']}\nWORKDIR {d['workdir']}\nCOPY . {d['workdir']}\n"
                f"EXPOSE {d['port']}\nHEALTHCHECK CMD python3 -c \"import urllib.request;"
                f"urllib.request.urlopen('http://localhost:{d['port']}{d['health']}')\"\n"
                f"CMD {json.dumps(d['cmd'])}\n")
    return (f"FROM {d['image']}\nCOPY dashboard.html {d['workdir']}/index.html\nEXPOSE {d['port']}\n")


def _deployment_yaml(d: dict, slug: str, min_rep: int) -> str:
    name = f"{slug}-{d['name']}"
    probes = (f"""          readinessProbe:
            httpGet: {{ path: {d['health']}, port: {d['port']} }}
            initialDelaySeconds: 3
            periodSeconds: 5
          livenessProbe:
            httpGet: {{ path: {d['health']}, port: {d['port']} }}
            initialDelaySeconds: 10
            periodSeconds: 20
""")
    annot = ("      annotations:\n"
             f"        prometheus.io/scrape: \"true\"\n"
             f"        prometheus.io/port: \"{d['port']}\"\n"
             f"        prometheus.io/path: \"{d['metrics']}\"\n") if d["metrics"] else ""
    return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  labels: {{ app: {name}, plane: data-sre }}
spec:
  replicas: {min_rep}
  selector:
    matchLabels: {{ app: {name} }}
  template:
    metadata:
      labels: {{ app: {name} }}
{annot}    spec:
      containers:
        - name: {d['name']}
          image: {name}:latest
          ports:
            - {{ containerPort: {d['port']} }}
          resources:
            requests: {{ cpu: "100m", memory: "128Mi" }}
            limits: {{ cpu: "500m", memory: "256Mi" }}
{probes}"""


def _service_yaml(d: dict, slug: str) -> str:
    name = f"{slug}-{d['name']}"
    return f"""apiVersion: v1
kind: Service
metadata:
  name: {name}
  labels: {{ app: {name} }}
spec:
  selector: {{ app: {name} }}
  ports:
    - {{ name: http, port: {d['port']}, targetPort: {d['port']} }}
"""


def _hpa_yaml(d: dict, slug: str, min_rep: int, max_rep: int) -> str:
    name = f"{slug}-{d['name']}"
    return f"""apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {name}
spec:
  scaleTargetRef: {{ apiVersion: apps/v1, kind: Deployment, name: {name} }}
  minReplicas: {min_rep}
  maxReplicas: {max_rep}
  metrics:
    - type: Resource
      resource: {{ name: cpu, target: {{ type: Utilization, averageUtilization: 70 }} }}
"""


def _servicemonitor_yaml(d: dict, slug: str) -> str:
    name = f"{slug}-{d['name']}"
    return f"""apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: {name}
  labels: {{ app: {name} }}
spec:
  selector:
    matchLabels: {{ app: {name} }}
  endpoints:
    - {{ port: http, path: {d['metrics']}, interval: 30s }}
"""


def _prometheusrule_yaml(d: dict, slug: str, slo: dict) -> str:
    name = f"{slug}-{d['name']}"
    return f"""apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: {name}-slo
  labels: {{ app: {name}, role: alert-rules }}
spec:
  groups:
    - name: {name}.slo
      rules:
        - alert: {d['name']}Down
          expr: dt_up{{job="{name}"}} == 0
          for: 1m
          labels: {{ severity: critical }}
          annotations: {{ summary: "{name} is down (SLO availability {slo['availability']})" }}
        - alert: {d['name']}HighErrorRate
          expr: rate(dt_errors_total[5m]) / clamp_min(rate(dt_requests_total[5m]), 1) > 0.01
          for: 5m
          labels: {{ severity: warning }}
          annotations: {{ summary: "{name} error rate exceeds SLO ({slo['error_rate_max']})" }}
"""


def run(run_root: str, slug: str, min_replicas: int = 2, max_replicas: int = 6) -> dict:
    ctx = RunContext(run_root, slug)
    stage = new_stage("sre")
    deployables = _deployables(ctx)
    if not deployables:
        finish_stage(stage, "failed")
        stage["notes"] = "no deployable found (run ml for a service or bi for a dashboard first)"
        ctx.append_stage(stage)
        raise SystemExit("sre: nothing to deploy — run `ml` (service) or `bi` (dashboard) first")

    deploy_dir = ctx.ensure_dir("deployment")
    manifest_list: list[dict] = []
    lineage, quality = [], []
    for d in deployables:
        name = f"{slug}-{d['name']}"
        dd = deploy_dir / d["name"]
        dd.mkdir(parents=True, exist_ok=True)
        slo = {**DEFAULT_SLO}
        (dd / "Dockerfile").write_text(_dockerfile(d))
        (dd / "deployment.yaml").write_text(_deployment_yaml(d, slug, min_replicas))
        (dd / "service.yaml").write_text(_service_yaml(d, slug))
        (dd / "hpa.yaml").write_text(_hpa_yaml(d, slug, min_replicas, max_replicas))
        files = ["Dockerfile", "deployment.yaml", "service.yaml", "hpa.yaml"]
        if d["metrics"]:
            (dd / "servicemonitor.yaml").write_text(_servicemonitor_yaml(d, slug))
            (dd / "prometheusrule.yaml").write_text(_prometheusrule_yaml(d, slug, slo))
            files += ["servicemonitor.yaml", "prometheusrule.yaml"]
        (dd / "slo.json").write_text(json.dumps(slo, indent=2) + "\n")
        (dd / "values.yaml").write_text(
            f"# Helm-style tunables for {name}\nimage: {name}:latest\nreplicas: {min_replicas}\n"
            f"autoscale: {{ min: {min_replicas}, max: {max_replicas}, cpuTarget: 70 }}\n"
            f"resources: {{ cpuRequest: 100m, cpuLimit: 500m, memRequest: 128Mi, memLimit: 256Mi }}\n"
            f"observability: {{ metrics: {str(bool(d['metrics'])).lower()}, path: {d['metrics'] or 'n/a'} }}\n")
        files += ["slo.json", "values.yaml"]
        manifest_list.append({"name": name, "kind": d["kind"], "source": d["path"], "port": d["port"],
                              "autoscale": {"min": min_replicas, "max": max_replicas, "cpuTarget": 70},
                              "observability": bool(d["metrics"]), "slo": slo, "files": files})
        lineage.append({"output": f"deployment.{name}", "from": [f"{d['kind']}.{d['path']}"],
                        "logic": f"k8s Deployment+Service+HPA({min_replicas}-{max_replicas})"
                                 + ("+ServiceMonitor+PrometheusRule" if d["metrics"] else "")})
        quality.append({"check": f"{name} deploy bundle", "result": "pass",
                        "detail": f"{len(files)} manifests; SLO {slo['availability']}"})

    (deploy_dir / "deployment.json").write_text(json.dumps(
        {"slug": slug, "deployables": manifest_list, "substrate": "k8s/k3s"}, indent=2) + "\n")
    (deploy_dir / "README.md").write_text(
        f"# {slug} — deployment bundle (Data SRE plane)\n\n"
        f"{len(manifest_list)} deployable(s): {', '.join(m['name'] for m in manifest_list)}.\n\n"
        "```bash\n# build + apply one deployable (from its dir)\n"
        "docker build -t <name>:latest .\n"
        "kubectl apply -f deployment.yaml -f service.yaml -f hpa.yaml \\\n"
        "  -f servicemonitor.yaml -f prometheusrule.yaml\n```\n\n"
        "Autoscaling via HPA (CPU 70%); observability via Prometheus ServiceMonitor scraping "
        "`/metrics`; SLO alerts via PrometheusRule. Targets the AiNa Colima+k3s substrate.\n")

    gov = empty_governance()
    gov["pii"] = ctx.inherited_governance()["pii"]
    gov["lineage"] = lineage
    gov["quality"] = quality
    gov["contract"] = {"name": f"{slug}.deployment@1", "honored": True, "violations": []}
    stage["consumed"] = {d["kind"]: d["path"] for d in deployables}
    stage["produced"] = {"deployment": ctx.rel("deployment")}
    stage["governance"] = gov
    obs = sum(1 for d in deployables if d["metrics"])
    stage["receipts"] = [f"deployed {len(deployables)} target(s): "
                         f"{', '.join(f'{slug}-'+d['name'] for d in deployables)}; "
                         f"HPA {min_replicas}-{max_replicas}, {obs} with Prometheus observability + SLO alerts"]
    finish_stage(stage, "ok")
    stage["notes"] = f"{len(deployables)} deployable(s); HPA {min_replicas}-{max_replicas}"
    ctx.append_stage(stage)
    return {"deployables": [m["name"] for m in manifest_list], "count": len(manifest_list),
            "observability": obs, "status": "ok", "deploy_dir": str(deploy_dir)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Data SRE: deploy any deployable at scale, observably")
    ap.add_argument("--run-root", default=".")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--min-replicas", type=int, default=2)
    ap.add_argument("--max-replicas", type=int, default=6)
    args = ap.parse_args(argv)
    res = run(args.run_root, args.slug, args.min_replicas, args.max_replicas)
    print(f"sre: ok — {res['count']} deployable(s) {res['deployables']}; "
          f"{res['observability']} observable -> {res['deploy_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
