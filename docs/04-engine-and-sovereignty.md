# Per-Stage Engine Selection & Sovereignty (P0.5)

Each stage names a **role of work**, and different roles want different engines: profiling
a CSV is cheap and mechanical; planning a metric's SQL or ruling out an RCA is reasoning.
The skill lets the user pick an engine **per stage**, defaulting to the sovereign, local,
zero-cost path — mirroring `aina-skill`'s engine model and hub2's local-first tenet.

## The engine ladder (per stage)

Resolved in this order (first hit wins), independently for each stage:

1. **`--engine <stage>=<engine>`** CLI override, or
2. **`data-team.yaml`** in the CWD (`engines: { designer: cloud, analyst: local }`), or
3. **env var** `DATA_TEAM_ENGINE_<STAGE>` (e.g. `DATA_TEAM_ENGINE_DESIGNER`), or
4. **plugin userConfig** (`${CLAUDE_PLUGIN_OPTION_ENGINE_<STAGE>}`, set only when installed
   as a plugin), else
5. the **default** below.

## Engines

| engine | what runs the reasoning | when |
|---|---|---|
| **`local`** *(default)* | **YOU (the user's Claude session)** are the engine — read the persona, do the work, thread prior stages forward. No API key, no extra cost, fully sovereign. | default for every stage |
| **`hub2`** *(optional)* | Route the stage's reasoning through a local hub2 endpoint (Switchboard `:8000` / a foundry model) — for users running the hub2 stack who want a specific local family. | opt-in, still sovereign (on-box) |
| **`cloud`** *(optional)* | Offload the bulk/mechanical reasoning to a cheaper cloud model (OpenRouter-style POST) to save the user's Claude quota. Requires a key; **silently falls back to `local` if absent.** | opt-in only |

**Mechanical work is always deterministic code, never a model.** The Data Engineer's
medallion transform, the Architect's profiler, and the Designer's conformance guard run as
stdlib/DuckDB **scripts** — the engine setting only governs the *reasoning* portions
(choosing cleaning rules, authoring metric definitions, planning an analyst's query,
narrating a report). This keeps the numbers reproducible regardless of engine.

## Sovereignty rules (non-negotiable defaults)

- **Local-first by construction:** cloud never fires unless the user explicitly set
  `cloud` *and* a key is present. Absent key → local. No surprise egress.
- **Data stays put on `local`/`hub2`:** raw/bronze/silver/gold never leave the box on the
  sovereign engines. On `cloud`, only the minimal reasoning context is sent — never bulk
  rows — and the stage manifest records that a cloud engine touched it.
- **Recorded in the manifest:** every stage writes its resolved `engine`
  (`{role, model, provider}`) so a run is reproducible and a reviewer can see exactly what
  reasoned over the data.

## Suggested defaults per stage (all overridable)

| stage | sensible default | rationale |
|---|---|---|
| architect | `local` | profiling is mostly deterministic code |
| engineer | `local` | transforms are code; only rule-choice reasons |
| designer | `local` | metric authoring wants strong reasoning — keep it on Claude |
| analyst | `local` | query planning + narrative want strong reasoning |
| bi / sre | `local` | config generation |
| scientist / ml | `local` | model choice + eval reasoning; training is code/tools |

The point is not to push work off-box — it is to let a quota-conscious user *choose* to,
per stage, while the default keeps everything sovereign and free.
