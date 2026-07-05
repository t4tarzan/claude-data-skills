# Role → Stage Catalog (P0.1)

The `/data-team` skill packages the modern data team as **eight optional, reorderable
stages**. Each stage is one role. You pick any subset in any order; a dependency
resolver (see [P0.3](02-selection-resolver.md)) keeps the selection coherent.

## Topology — it is not a single line

The eight stages form a **spine**, two **branches** that hang off the gold layer, and
one **platform plane** that wraps whatever you selected. Governance is a **cross-cutting
plane** threaded through every stage's contract — never a skippable box.

```
        SPINE  (data flows down)
        ┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
raw ───▶│ Data Architect│──▶│ Data Engineer │──▶│ Data Designer │──▶│  Data Analyst │──▶ answers
        │   → bronze    │   │  → silver/gold│   │ → semantic    │   │ → reports     │
        └───────────────┘   └──────┬────────┘   └───────────────┘   └───────────────┘
                                   │ gold
                    ┌──────────────┴───────────────┐
             BRANCH A│                        BRANCH B
        ┌────────────▼───────┐        ┌────────────▼────────┐   ┌──────────────────┐
        │   BI Engineer      │        │  Data Scientist     │──▶│   ML Engineer     │
        │ → served dashboards│        │  → trained model    │   │ → served model API│
        └────────────────────┘        └─────────────────────┘   └──────────────────┘

  PLATFORM PLANE  ── Data SRE ── wraps ANY deployable (pipeline / dashboard / model service)
  GOVERNANCE PLANE ── cross-cuts EVERY stage (lineage · PII · contracts · quality SLAs)
```

**Natural order** is the spine, then a branch, then the SRE plane. But every stage is
optional: run just the Architect to land+catalog files; just the Analyst against a gold
layer you already have; the ML branch without ever touching BI. The resolver figures out
what each selected stage needs and either uses a supplied upstream artifact, synthesizes
the missing stage, or refuses with a clear reason.

## The eight roles

Each stage declares what it **consumes** and **produces** — that contract (see
[P0.2](01-artifact-contract.md)) is what makes "any subset, any order" safe.

| # | Stage (role) | One-liner | Consumes → Produces | Extracted from (AiNa) |
|---|---|---|---|---|
| 1 | **Data Architect** | Ingest & catalog raw sources; profile types/keys/quality; land typed bronze. | raw files → `bronze/` + catalog | `data-landscape` :9016, `knowledge` :9011 |
| 2 | **Data Engineer** | Medallion transform: bronze → silver (clean) → gold (modeled, aggregated, query-optimized) + lineage. | bronze → `silver/` `gold/` + lineage | `metrics-mart` :9012 (martdb / DuckDB) |
| 3 | **Data Designer** | Governed **semantic layer**: metrics, dimensions, business definitions as versioned DSL over gold. | gold → metric/dimension defs + conformance | `metric-lab` / metric store (the AiNa DSL) |
| 4 | **Data Analyst** | Management questions → SQL/NLQ over gold+semantic layer → reports, charts, dashboards. | gold/defs → reports + visuals | `nlq` :9018, `widgets` |
| 5 | **BI Engineer** *(branch A)* | Refreshing, role-scoped BI: scheduled aggregation, live refresh, RBAC per role. | defs/reports → served dashboard + access model | `frontend` + `command-center` RBAC |
| 6 | **Data Scientist** *(branch B)* | Train & evaluate a predictive model on gold for forecasting / decision support. | gold → trained model + eval report | `experiments` :9017, `foundry` |
| 7 | **ML Engineer** *(branch B)* | Productionize the model: packaged, versioned, served behind an API, monitored, extensible. | model → deployable model service | `foundry`, `finetune-platform` |
| 8 | **Data SRE** *(platform plane)* | Deploy & operate on k3s/k8s at scale: Helm/manifests, autoscaling pods, observability, SLOs & alerting. | any deployable → running, observable, scaled deployment | Colima+k3s substrate, PM2 plane |

*Naming: BI Engineer / ML Engineer keep their industry-standard titles (more
recognizable than a forced "Data BI"). Data Designer was renamed from "Analytics
Engineer" for plain-language clarity — it designs how the business sees its data.*

## Governance — the cross-cutting plane (not a stage)

Every stage emits a **governance envelope** into its manifest: lineage (what produced
this from what), PII/sensitivity flags on columns, the data contract it honored, and any
quality-SLA results. The optional final **governance report** (P9) simply aggregates
those envelopes across the selected stages. This keeps governance *everywhere* instead of
one box a user can skip — matching AiNa's three-pillar thesis (semantic definitions ·
**data governance** · operational discipline).

## Stage identifiers (canonical keys)

Used by the manifest, the resolver, the config, and the CLI (`--stages`):

```
architect  engineer  designer  analyst  bi  scientist  ml  sre
```

Governance is not a stage key; it is the `governance` block inside every stage manifest,
plus an optional `--governance-report` flag.

## v1 scope

**v1 ships the spine only** — `architect → engineer → designer → analyst` — publishable
on its own (buildplan phases P0–P5). Branches (`bi`, `scientist`, `ml`), the `sre` plane,
and the aggregated governance report follow in P6–P9. This catalog is the stable contract
they all build against.
