# data-team-skill

The modern **data team as one composable, model-selectable Claude Code skill**. Pick any
subset of eight roles, in any order; the skill runs them as a coherent pipeline over your
data and hands back a single, auditable run directory. Born the same way as `aina-skill`:
build the pipeline, then crystallize it into a skill.

**Invocation (target):** `/data-team:run` · **Install (target):** `/plugin marketplace add
t4tarzan/data-team` → `/plugin install data-team@data-team`

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

**P0 foundation — done.** Next: **P1 Data Architect** (ingest & catalog → bronze),
extracted from AiNa's `data-landscape`/`knowledge` backends. Task board:
`~/hub2/projects/ai-native-analytics/runtime/buildplan.db` table `tasks_v2`.

**Strategy:** extract-from-AiNa — every stage maps to an existing `9011–9018` backend, so
this ships what we already dogfooded on Flipkart data. v1 = the spine.
