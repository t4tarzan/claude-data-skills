# Distribution & Installer (P0.6)

Verified against the current Claude Code docs (code.claude.com/docs, 2026-07-04) via the
`claude-code-guide` research pass.

## The mechanisms (as they stand today)

| | Plain skill | **Plugin (git marketplace)** | npm |
|---|---|---|---|
| On disk | `~/.claude/skills/<n>/SKILL.md` | plugin dir + `.claude-plugin/plugin.json` | npm pkg referenced by a marketplace entry |
| Invocation | `/<name>` (clean) | `/<plugin>:<skill>` (namespaced) | same as plugin |
| Install | manual copy / git checkout | `/plugin marketplace add` → `/plugin install` | marketplace entry `"type":"npm"` |
| Versioning | none | semver in manifest | npm + manifest |
| One-command install | ✗ | ✅ | via marketplace |

**Key facts confirmed:**
- A skill auto-becomes a slash command. Standalone → `/data-team`; inside a plugin →
  `/<plugin>:<skill>`.
- Custom commands and skills are now unified — `commands/x.md` and `skills/x/SKILL.md`
  both yield `/x`.
- A plugin bundles skills + agents + hooks + MCP together, with a `plugin.json` manifest
  and a `marketplace.json` catalog. Marketplace sources: `github`, `git` (+ `subdir`),
  `npm`, `path`.

## Decision

**Ship `/data-team` as a git-hosted plugin** named `data-team`, with a single primary
skill `run`. This mirrors the proven sibling `aina-skill` (installed as a plugin,
invoked `/aina-skill:build`).

- **Invocation:** `/data-team:run` (namespaced — the plugin form's small tax; it is
  exactly the `/aina-skill:build` shape we already validated).
- **Publisher artifacts** (this cell becomes the plugin repo):
  ```
  data-team/                         # the published repo
  ├── .claude-plugin/
  │   ├── plugin.json                # name, version, description, "skills":"./skills/"
  │   └── marketplace.json           # catalog with one plugin entry (source: github)
  ├── skills/
  │   └── run/
  │       ├── SKILL.md               # the orchestrator (frontmatter: name, description, allowed-tools)
  │       ├── personas/<stage>/*.md  # the 8 role personas (the IP)
  │       └── scripts/*.py           # stdlib medallion/profiler/etc. helpers
  └── README.md                      # the one-line installer + manual
  ```
- **End-user one-liner** (goes on the README and the `:9020` sidebar page):
  ```
  /plugin marketplace add t4tarzan/claude-data-skills
  /plugin install data-team@claude-data-skills
  ```
  then `/data-team:run`.
- **npm option (kept open):** the marketplace entry can later switch its `source` to
  `{"type":"npm","package":"@dkube/data-team"}` to also publish on npm — satisfies the
  "publish on npm" goal without changing the skill. Git marketplace is the default
  because it gives the cleanest one-command install today.

## Layout implication for this repo

`${CLAUDE_SKILL_DIR}` resolves to `skills/run/` at runtime (whether installed via plugin
or copied into `~/.claude/skills/`). Personas and scripts are addressed relative to it,
exactly as `aina-skill` does. The medallion/profiler code extracted from AiNa lands in
`skills/run/scripts/`; each role's reasoning lands in `skills/run/personas/<stage>/`.

**Deferred to P5.2–P5.3:** authoring `plugin.json` / `marketplace.json` / `SKILL.md` and
the README installer. This doc fixes the *target* so P1–P4 build the scripts/personas
into the right home.
