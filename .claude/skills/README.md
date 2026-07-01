# Combine skills

Agent Skills that wrap the `combine-mcp` MCP server with
task-specific instructions, following the open
[SKILL.md standard](https://www.agensi.io/learn/agent-skills-open-standard).

## Available skills

| Skill | What it does |
|---|---|
| [`combine/`](combine/SKILL.md) | General-purpose Combine assistant. Routes questions across the four sources (docs / paper / code / forum) and answers with citations. |

## Prerequisites

The `combine-mcp` server must be installed and registered with your
client. See the top-level [README](../../README.md) for setup.

## Installation

These skills follow the open SKILL.md format and work in any
SKILL.md-aware client, including Claude Code, opencode, Codex CLI,
Gemini CLI, Cursor, Cline, and Windsurf.

### Project-scoped (no install required)

Your client auto-discovers skills from `.claude/skills/` at the project
root. If you cloned this repo, the skills are already active when you
work inside the project.

### User-scoped (skills available in any project)

Copy the skill directory into your user-level skills directory:

```bash
mkdir -p ~/.claude/skills
cp -r .claude/skills/combine ~/.claude/skills/
```

That path works for both Claude Code and opencode. opencode also reads
`~/.config/opencode/skills/` if you prefer to namespace by tool.

## Discovery paths

Most SKILL.md-aware clients auto-discover skills under one or more of:

- `.claude/skills/<name>/SKILL.md` — project-local (what this repo uses)
- `~/.claude/skills/<name>/SKILL.md` — user-level
- `.opencode/skills/<name>/SKILL.md` — opencode-specific, project
- `~/.config/opencode/skills/<name>/SKILL.md` — opencode-specific, user
- `.agents/skills/<name>/SKILL.md` — some tools

The skill content is identical across paths. Pick whichever your
client supports.

## Contributing a new skill

Open a PR with a new skill directory under this folder. Follow the
SKILL.md schema:

- `name`: required, 1–64 chars, lowercase alphanumeric with single
  hyphens (`^[a-z0-9]+(-[a-z0-9]+)*$`), must match the directory name.
- `description`: required, 1–1024 chars. This is what triggers the
  skill — be specific about when it should be used.
- `license`: optional. We use `MIT`.
