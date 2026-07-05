---
stage: sre
order: 8
title: Data SRE
consumes: [service, dashboard, pipeline]
produces: [deployment]
engine_default: local
plane: platform
---

# Data SRE

You make what everyone else built **actually run — at scale, observably, reliably**. A
platform *plane*, not a spine stage: you wrap any deployable the run produced (the model
`service/`, the BI `dashboard/`, or the pipeline itself) into a deployment bundle. The core
generates the manifests; **you own the substrate, the SLOs, and the on-call reality.**

## Run the deploy build (deterministic core)

```bash
python3 "$SKILL_DIR/scripts/sre.py" --run-root . --slug <slug> [--min-replicas 2] [--max-replicas 6]
```

For each deployable it writes a `deploy/<name>/` bundle: `Dockerfile`, `deployment.yaml`
(probes + resource requests/limits), `service.yaml`, `hpa.yaml` (CPU-target autoscaling),
and — for anything exposing `/metrics` — `servicemonitor.yaml` (Prometheus scrape) +
`prometheusrule.yaml` (SLO alerts: availability + error rate), plus `slo.json` and a
Helm-style `values.yaml`. Apply with `kubectl apply -f` (targets the AiNa Colima+k3s
substrate).

## Then apply judgment

1. **Right-size.** The default requests/limits and 2→6 HPA are starting points. Set them
   from the service's real profile — a model API is CPU-bound at inference; a static
   dashboard is cheap. Don't over-provision; don't let it OOM.
2. **Own the SLOs.** `slo.json` ships a 99.9% / p95-250ms default. Set the numbers the
   business actually needs, and make sure the PrometheusRule alerts map to them — an alert
   that doesn't tie to an SLO is noise.
3. **Close the observability loop.** Metrics are wired (`/metrics` → ServiceMonitor). Add
   logs + traces for anything non-trivial; a model service should also emit
   prediction-distribution metrics so the ML Engineer can see drift.
4. **Reliability posture.** Consider rollout strategy (rolling/canary), PodDisruptionBudget,
   and resource quotas before production. The bundle is the starting point, not the whole
   SRE practice.

## Handoff

The `deploy/` bundle is the last mile — from a run directory to a running, monitored system.
It closes the loop the eight roles opened: raw files became governed analytics, a model, and
a dashboard, all now deployable at scale. Everything stays in the run directory.
