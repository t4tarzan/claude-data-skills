# data-team

The modern **data team as one composable, model-selectable Claude Code skill**. Pick any
subset of eight roles, in any order; the skill runs them as a coherent pipeline over your
data and hands back a single, auditable run directory. Born the same way as `aina-skill`:
build the pipeline, then crystallize it into a skill.

## Install (one command)

```
/plugin marketplace add t4tarzan/data-team
/plugin install data-team@data-team
```

Then invoke: **`/data-team:run`** — point it at a folder of data and go.

```
/data-team:run
> where is your data?  ./sales_exports
> which roles?         (default: the full spine)
> what to answer?      "gmv by platform", "top 5 tier by aov"
```

Or drive the deterministic core directly (no install needed — stdlib Python, no venv):

```bash
python3 skills/run/scripts/run_pipeline.py --slug demo --sources ./examples/retail \
    --metrics-dir ./examples/retail_metrics --ask "gmv by platform" --ask "average order value by tier"
# -> ./data-team-out/demo/{bronze,silver,gold,semantic,reports,visuals}/ + manifest.json
```

## What you get

One **run directory** — a portable deliverable: `bronze/` (typed SQLite) → `silver/` →
`gold/` (modeled facts + conformed dims) → `semantic/` (governed metric DSL) → `reports/`
(narrative + tables + a **receipt** per answer) + `visuals/` (SVG charts), all tied together
by `manifest.json` — the full audit trail (lineage, PII flags, reconciliation receipts,
per-metric conformance). Zip it and everything travels together.

## The eight roles

`architect · engineer · designer · analyst` (the spine) · `bi` · `scientist → ml`
(branches off gold) · `sre` (platform plane) — with **governance** cross-cutting every
stage. Full catalog: [`docs/00-role-stage-catalog.md`](docs/00-role-stage-catalog.md).

## Foundation specs (P0)

| doc | what it fixes |
|---|---|
| [00 role→stage catalog](docs/00-role-stage-catalog.md) | the 8 roles, topology, one-liners, AiNa origin |
| [01 artifact contract](docs/01-artifact-contract.md) | what each stage consumes/produces + the stage manifest |
| [02 selection & resolver](docs/02-selection-resolver.md) | "any subset, any order" made coherent by a DAG resolver |
| [03 run layout](docs/03-run-layout.md) | the run directory, `manifest.json`, governance envelope |
| [04 engine & sovereignty](docs/04-engine-and-sovereignty.md) | per-stage engine, local-first defaults |
| [05 distribution](docs/05-distribution.md) | ship as a git-hosted plugin; the one-line installer |

Schema: [`schema/stage-manifest.schema.json`](schema/stage-manifest.schema.json) ·
Config: [`data-team.example.yaml`](data-team.example.yaml)

## Status

**v1 = the spine, complete and tested.** `architect → engineer → designer → analyst` run
end-to-end (26 tests green); packaged as a plugin (`.claude-plugin/`, `SKILL.md`,
`run_pipeline.py` with the DAG resolver). Branches (`bi`, `scientist → ml`), the `sre`
plane, and the aggregated governance report resolve but are not yet runnable — they land in
the next phases.

**Strategy:** extract-from-AiNa — every stage maps to an existing AiNa `9011–9018` backend,
so this ships what we already dogfooded on Flipkart data. Task board:
`~/hub2/projects/ai-native-analytics/runtime/buildplan.db` table `tasks_v2`.
