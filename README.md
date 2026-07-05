# Claude Data Skills

**A whole data team as one Claude Code command.** Pick any subset of eight data roles, in any
order; they run as one coherent pipeline over your data — raw files → a typed **medallion**
(bronze/silver/gold) → a **governed semantic layer** → reports, charts, a trained model, a
dashboard, and k8s deploy manifests — all in a single, auditable run directory.

This repo is a **Claude Code plugin marketplace**. Its first plugin is **`data-team`**
(invoked `/data-team:run`). Everything is **stdlib Python — no venv, no API key, no cloud**;
your local Claude is the engine.

---

## Install

**One line** (installs via the `claude` CLI; re-run any time to pull the latest):

```bash
curl -fsSL https://raw.githubusercontent.com/t4tarzan/claude-data-skills/main/install.sh | bash
```

**Or inside a Claude Code session** — two commands:

```
/plugin marketplace add t4tarzan/claude-data-skills
/plugin install data-team@claude-data-skills
```

Then invoke it:

```
/data-team:run
```

### Keep it up to date

- **Auto-update:** run `/plugin` → **Marketplaces** → `claude-data-skills` → **Enable
  auto-update**. Claude Code then refreshes the catalog at startup and pulls new versions
  automatically.
- **On demand:** `/plugin marketplace update claude-data-skills`
- **The one-liner** above is also idempotent — re-running it refreshes to the latest.

> **npm?** Not needed. A git-hosted marketplace *is* the native, one-command install path for
> Claude Code — zero npm involvement, versioned by git.

---

## Quick start

```
/data-team:run
> where is your data?   ./sales_exports        (a folder of CSV/JSON/XML/XLSX; PDF/images optional)
> which roles?          (default: the full spine — architect → engineer → designer → analyst)
> what to answer?       "gmv by platform", "top 5 tier by aov"
```

You get one **run directory**, `./data-team-out/<name>/`:

```
bronze/  silver/  gold/       ← typed medallion (SQLite), gold reconciled to the raw fact
semantic/                     ← governed metric DSL (+ the re-aggregation guard)
reports/  visuals/            ← answers with a receipt each + SVG charts
bi/  models/  service/  deploy/   ← dashboard, trained model + served API, k8s manifests
manifest.json                 ← the full audit trail: lineage · PII · reconciliation · conformance · SLOs
governance/report.html        ← one consolidated governance verdict
```

Zip it and the data, definitions, reports, model, and lineage all travel together.

### Run it directly (no install)

Every stage is plain `python3` — clone and go:

```bash
git clone https://github.com/t4tarzan/claude-data-skills && cd claude-data-skills
python3 skills/run/scripts/run_pipeline.py --slug demo \
    --sources ./examples/retail --metrics-dir ./examples/retail_metrics \
    --ask "gmv by platform" --ask "average order value by tier"
python3 -m unittest discover -s tests     # 51 tests
```

---

## The eight roles

Pick any subset with `--stages`; a **dependency resolver** synthesizes whatever upstream a
selection needs (ask for just the Analyst on a folder of CSVs and it builds the medallion
first). Governance is **cross-cutting** — every stage records lineage, PII flags, contracts,
and quality checks, aggregated into one verdict.

| # | Role (stage key) | What it does | Consumes → Produces |
|---|---|---|---|
| 1 | **Data Architect** `architect` | Ingest & catalog raw sources; profile types/keys/quality; land typed bronze. | raw → `bronze` + catalog |
| 2 | **Data Engineer** `engineer` | Medallion bronze → silver → gold + lineage; every measure reconciled to the raw fact. | bronze → `silver`, `gold` |
| 3 | **Data Designer** `designer` | Governed semantic layer / metric DSL with the re-aggregation guard (a ratio recomputes, a distinct-count → Theta/HLL — never summed). | gold → `semantic` |
| 4 | **Data Analyst** `analyst` | NL questions → reports + charts, a **receipt** per answer (definition, owner, version, SQL, lineage). | gold → `reports`, `visuals` |
| 5 | **BI Engineer** `bi` *(branch)* | Refreshing, RBAC-scoped dashboard off gold. | gold → `dashboard` |
| 6 | **Data Scientist** `scientist` *(branch)* | Train & evaluate a predictive model (from-scratch ridge OLS) on gold. | gold → `model`, `eval` |
| 7 | **ML Engineer** `ml` *(branch)* | Package & serve the model behind a stdlib HTTP API. | model → `service` |
| 8 | **Data SRE** `sre` *(plane)* | Deploy any of the above on k8s: manifests + HPA autoscaling + Prometheus observability + SLO alerts. | service → `deployment` |

Topology: a **spine** (1→4), two **branches** off gold (BI; Scientist→ML), and a **platform
plane** (SRE) that wraps any deployable.

---

## Why it's trustworthy

- **Sovereign by default** — local Claude is the engine; no data leaves the box unless you
  opt a stage into a cloud model. Mechanical work (transforms, profiling, reconciliation) is
  deterministic code, so the numbers are reproducible regardless of engine.
- **Governed by construction** — you cannot produce a gold table without recording where it
  came from; a ratio cannot be silently summed up a grain; every answer carries a receipt.
- **Portable** — stdlib only. SQLite medallion, JSON metric defs, a from-scratch linear model,
  an `http.server` model API. Optional extras (`pypdf`, `Pillow`+`pytesseract`, `PyYAML`)
  enable PDF/image/YAML and degrade gracefully if absent.

## Configuration

Drop a `data-team.yaml` in your working dir (see `data-team.example.yaml`): choose `stages`,
per-stage `engines` (local / cloud), the missing-input `policy`, and bring-your-own artifacts
(e.g. supply an existing `gold/` and run only the Analyst).

## Layout

```
.claude-plugin/{plugin,marketplace}.json   ← plugin + marketplace manifests
skills/run/SKILL.md                         ← the /data-team:run orchestrator
skills/run/scripts/*.py                     ← the deterministic core (stdlib)
skills/run/personas/<stage>/*.md            ← each role's judgment (the IP)
docs/  schema/  examples/  tests/           ← specs, JSON schema, sample data, 51 tests
install.sh                                  ← the one-line installer
```

## License

MIT © t4tarzan
