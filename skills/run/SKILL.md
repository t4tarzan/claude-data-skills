---
name: run
description: Run a modern data team as one composable pipeline over your data. Pick any subset of eight roles — Data Architect, Data Engineer, Data Designer, Data Analyst, BI Engineer, Data Scientist, ML Engineer, Data SRE — in any order; the skill ingests raw sources into a typed medallion (bronze→silver→gold), builds a governed semantic layer, and answers questions with reports, charts, and receipts. Use when the user says "/data-team", "data-team run", wants to profile/clean/model a dataset, build a metrics layer, or turn raw files (CSV/JSON/XML/XLSX/PDF) into governed analytics. v1 ships the spine (architect→engineer→designer→analyst).
allowed-tools: Read, Write, Edit, Bash(python3 *), Bash(mkdir *), Bash(ls *), Bash(sqlite3 *), Bash(find *), AskUserQuestion
---

# data-team — run

Run the modern data team as **one composable pipeline** over the user's data. Each of the
eight roles is an optional, reorderable **stage**; a dependency resolver keeps any selection
coherent. **Your local Claude is the engine** — you run the deterministic core (stdlib
Python, no venv, no API key) and then apply each role's judgment.

## Setup

- `SKILL_DIR` = `${CLAUDE_SKILL_DIR}` — this skill's dir (works whether installed as a plugin
  or copied into `~/.claude/skills/`). Scripts live in `$SKILL_DIR/scripts/`, role personas
  in `$SKILL_DIR/personas/<stage>/`.
- `PY` = `python3` (all scripts are **stdlib-only**; optional libs — `pypdf`, `Pillow`+
  `pytesseract`, `PyYAML` — enable PDF/image/YAML but degrade gracefully if absent).
- **Config** resolves in order: (1) `data-team.yaml` in the CWD, (2) CLI flags, (3) env var
  `DATA_TEAM_ENGINE_<STAGE>`, (4) plugin userConfig, else defaults. See
  `$SKILL_DIR/../../docs/04-engine-and-sovereignty.md`.

## The eight roles (stage keys)

`architect · engineer · designer · analyst` (the **spine**) · `bi` · `scientist → ml`
(branches off gold) · `sre` (platform plane). **v1 implements the spine**; the rest resolve
but are not yet runnable.

| stage | does | consumes → produces |
|---|---|---|
| architect | ingest & catalog raw → typed bronze SQLite + profile | sources → bronze, catalog |
| engineer | medallion bronze→silver→gold + lineage, reconciled | bronze → silver, gold |
| designer | governed semantic layer / metric DSL + guard | gold → semantic, conformance |
| analyst | NL questions → reports + charts + receipts | gold → reports, visuals |

## 1. Intake

Ask (AskUserQuestion) only what's missing: **where is the data** (files/folder), **which
roles** (default: the full spine), and **what questions** the analyst should answer (else a
few are auto-generated). If a `data-team.yaml` is present, read it first.

## 2. Run the pipeline (deterministic core)

```bash
python3 "$SKILL_DIR/scripts/run_pipeline.py" --slug <slug> \
    --sources <path...> [--stages architect,engineer,designer,analyst] \
    [--metrics-dir <dir>] [--ask "<question>"] [--policy synthesize|strict] [--gold <dir>]
```

The runner resolves the selection into a DAG (auto-adding upstream stages under the default
`synthesize` policy), writes the plan into `./data-team-out/<slug>/manifest.json`, and runs
each stage. Output is one **run directory**: `bronze/ silver/ gold/ semantic/ reports/
visuals/` + `manifest.json` (the full audit trail: lineage, PII flags, reconciliation
receipts, per-metric conformance).

## 3. Apply each role's judgment (the IP)

For each stage that ran, read `$SKILL_DIR/personas/<stage>/<stage>.md` and do the part code
cannot — the Architect assigns owners + PII disposition; the Engineer adds business-rule
cleaning + logic versions; the Designer authors the ratios/distinct-counts and writes crisp
definitions; the Analyst interprets, poses the real questions, and adds the "so what."
Thread each stage's output forward. **Honor the governance guard**: never sum a ratio or a
distinct-count up a grain — the semantic layer already recomputes them.

## 4. Deliver

Point the user at `./data-team-out/<slug>/reports/report.md` (+ the charts and the
`manifest.json` audit trail). The run directory is a portable deliverable — zip it and the
data, definitions, reports, and lineage travel together.

## Engine (sovereignty)

Default is `local` (you are the engine — sovereign, free). A quota-conscious user may set a
stage to `cloud` in `data-team.yaml`/env to offload *reasoning* (never bulk data); it
silently falls back to local without a key. Mechanical work (transforms, profiling,
reconciliation) is always deterministic code, never a model — so numbers are reproducible
regardless of engine.
