# SST Agent Review

**Keep your coding agent. Get a second opinion. Fix what matters.**

TL;DR: A skill for Codex and Claude Code that guides you through local code review, deciding what to fix, and checking the fixes. Install it on Linux or macOS:

```sh
curl -fsSL https://github.com/sintoniastrategy/agent-review/releases/latest/download/install.sh | bash
```

Start a new agent session after installation, then ask:

> Use SST Agent Review to review this worktree against main.

The skill checks prerequisites, explains each step and recommends what to do next. You steer it in conversation. Prefer to install by hand? See [Installation](#installation).

## How it works

1. **Review.** A fresh interactive Claude or Codex session runs in tmux and reviews your branch changes, including uncommitted work, against your chosen base.
2. **Discuss.** Your existing coding agent keeps the task context. It walks through critical/high findings individually and presents medium/low findings in tables with recommendations.
3. **Choose and fix.** Approve, skip or defer findings. Change your mind whenever needed. Your coding agent fixes the approved items in suitable batches.
4. **Check again.** When you request another pass, the reviewer gets previous findings, your decisions and the fix diffs. Recorded fixes and reviewer confirmations remain separate.

Stop whenever you've fixed enough. Findings, decisions and rechecks stay in readable Markdown files under `.agr/`. No PR, GitHub Actions, MCP server or database required. Orchestration runs locally; inference uses the selected provider's service.

## Which review tool should I use?

**Choose SST when review takes several rounds and you want to decide what gets fixed with the agent that wrote the code.** It keeps findings, your decisions, actual fixes and reviewer confirmations connected across passes.

| What you need | Choose | Why it fits |
| --- | --- | --- |
| A quick second opinion on a diff | [Codex built-in `/review`](https://learn.chatgpt.com/docs/codex/cli) | Start from your existing Codex session; get prioritized findings without setting up SST. |
| Review comments where your team already discusses PRs | [Anthropic's Code Review plugin](https://claude.com/marketplace/plugins/code-review) | Specialist reviewers filter findings by confidence and post feedback to GitHub. |
| A broad pre-landing workflow that also applies fixes | [Garry Tan's gstack](https://github.com/garrytan/gstack/blob/main/review/SKILL.md) | Specialist passes, automatic or approved fixes, repeat reviews and saved skip decisions. A good fit if you want that broader workflow. |
| Review integrated into planning, TDD and implementation | [obra's Superpowers](https://github.com/obra/superpowers/tree/main/skills/requesting-code-review) | Review subagents and feedback-handling rules are part of its development methodology. |
| A large toolkit to build your own agent workflow | [Everything Claude Code / ECC](https://github.com/affaan-m/ECC/tree/main/commands) | Choose among local/PR checklists, specialist review agents and orchestrated verification. |
| Work through findings, choose fixes and verify them over several passes | **SST Agent Review** | Keep your author session and its context; get guided discussion, selected fix batches and a durable local record of what was decided and checked. |

For example: a reviewer returns 40 findings. With SST, your coding agent discusses the critical ones, groups the rest into tables, records what you skip and why, and fixes the items you approve. The next reviewer sees those decisions and fix diffs. You can tell an implemented fix apart from one the reviewer has confirmed.

For a small, one-off review, the built-in option is simpler. SST earns its extra tmux/runtime setup when you would otherwise manage repeated reviews and their decisions by hand. This comparison is about workflow; we have not benchmarked bug-finding accuracy. Linked workflows checked October 2026.

## Installation

The command above installs the latest release into `~/.local/share/sst-agent-review`, with skill symlinks in `~/.agents/skills/sst-agent-review` and `~/.claude/skills/sst-agent-review`.

You'll need **curl, Python 3.9+, Git, tmux and a Claude subscription or Codex ChatGPT login** for the selected reviewer. The skill manages its own pinned CLI and clean configuration. Prerequisites are checked, not installed automatically.

- **Docker is the default on Linux and macOS.** Docker requires a local Linux Docker Engine, rootless or rootful, with source mounted read-only. Podman and userns-remap are unsupported.
- **Existing CLI logins are reused:** credential files or the default macOS Keychain entries. Both Docker agents use the same host bridge: a private temporary credential file with refresh synchronized back to Keychain. macOS may request Keychain access approval on first use. Full macOS validation is still pending.
- **Native mode is retained but has not passed release validation.** Its isolation depends on the agent: Claude's Bash sandbox is best effort and may fall back to your user permissions. Codex requests its built-in sandbox without an unsandboxed retry.

Updates are checked on skill invocation at most once daily. New invocations use the updated version; running workflows retain theirs. If updating is blocked, the skill explains how to run the install command manually.

**Manual installation:** from a checkout, copy the entire bundle into your agent's skills directory, using a destination that does not already exist:

```sh
mkdir -p ~/.agents/skills
cp -R skills/agent-review ~/.agents/skills/sst-agent-review
```

For Claude Code, use `~/.claude/skills` instead. Manual copies do not auto-update.

## Configuration

**Edit INI files directly, or ask your coding agent to edit them.** Create the directory/file if needed. The skill reads these layers in order; later values override earlier ones:

| File | Applies to |
| --- | --- |
| [defaults.ini](skills/agent-review/defaults.ini) | Shared review defaults and separate sections for both agents; replaced when the skill updates |
| `~/.local/share/sst-agent-review/config.ini` | Your defaults for all projects |
| `<worktree>/.agr/config.ini` | This worktree only; ignored by Git |

For example, put this in your personal file to use Sonnet with Docker:

```ini
[review]
runtime = docker

[claude]
model = sonnet
effort = medium
```

To select Codex in one project, put this in its `.agr/config.ini`:

```ini
[review]
agent = codex
```

Model and effort inherit from the selected agent's personal section. `[review]` holds `agent`, `preset`, `scope`, `runtime` and `window_name`. `[claude]` and `[codex]` each hold their own `model`, `effort`, `auth` and `credentials_file`. Delete a key to inherit it again. Claude settings never become Codex settings when switching agents.

Shipped defaults are **Claude Opus, xhigh effort, full review, Docker**, as shown in [defaults.ini](skills/agent-review/defaults.ini). There is no `configure` command. Legacy agent settings under `[review]` remain readable: they belong to the agent selected at that configuration layer, inheriting the preceding layer's agent or Claude. A CLI agent override does not transfer them to another agent. Explicit agent sections take precedence over legacy keys in the same file.

Select Codex with `agent = codex` under `[review]`, or `prepare --agent codex` for one run. Its bundled defaults are **gpt-6.1-sol, xhigh effort**, using managed Codex CLI **0.160.0**. For example, add `[codex]` with `effort = medium` to keep that preference independently of Claude.

Authentication is agent-specific:

| Section | `auth` choices | `auto` discovery |
| --- | --- | --- |
| `[claude]` | `auto`, `file`, `keychain` | Explicit `credentials_file`; otherwise default macOS Keychain if present; otherwise `$CLAUDE_CONFIG_DIR/.credentials.json` or `~/.claude/.credentials.json` |
| `[codex]` | `auto`, `file`, `keyring` | Explicit `credentials_file`; otherwise `$CODEX_HOME/auth.json` or `~/.codex/auth.json` if present; otherwise that home's macOS Keychain entry |

Explicit Keychain/keyring authentication requires macOS. Custom Claude Keychain profiles are not discovered. Codex `auto` prefers an existing file without reading your Codex config; use `[codex] auth = keyring` to select Keychain explicitly when both exist. The managed CLI checks subscription/ChatGPT authentication before inference; API-key fallback is refused.

The Keychain bridge stores credentials under `~/.local/share/sst-agent-review/credentials/`, outside the mounted project, with private directories (`0700`) and files (`0600`). Each container receives only its own credential file. A shared lock for each Keychain item serializes bridge updates across journals and worktrees. The bridge synchronizes refreshed tokens and deletes the temporary file after successful session closure. A detected conflicting login or synchronization error retains the private file and reports its path for recovery. Credentials are never included in prompts or command arguments.

Ask the skill to check settings, or run this from the worktree with your actual local base:

```sh
python3 ~/.local/share/sst-agent-review/current/agent-review/scripts/review.py preflight --base main --human
```

It shows effective settings and checks prerequisites without starting a reviewer. Both `preflight` and `prepare` accept overrides for agent, model, effort, preset, scope and runtime; use matching flags for the intended run. Preparation repeats tool and credential checks before setting up the runtime. Existing prepared rounds retain their settings. Claude and Codex can share an explicitly requested parallel pass, with separate IDs such as `r02-claude1` and `r02-codex1`.

To explicitly test both real Docker runtimes, including interactive startup, command execution, publication and cleanup, run:

```sh
python3 ~/.local/share/sst-agent-review/current/agent-review/scripts/smoke.py --repo . --agent both --runtime docker --effort medium
```

This starts real model sessions, saves separate artifacts under `.agr/.cache/smoke-*`, and preserves the existing review cycle. Use `--agent claude` or `--agent codex` for one agent. Failed runs are retained without automatic retry.

**Upgrading from JSON configuration:** convert each `config.json` to `config.ini` at the same location. Write `[review]`, then one `key = value` per line, without JSON braces, commas or quotes. A legacy file without an INI replacement produces a migration error. Once the INI exists, only it is used. Review journal files are unaffected.

Prompt presets define only what to review and how. [default](skills/agent-review/presets/default/reviewer/review.md) is an independent comprehensive review. [lenses](skills/agent-review/presets/lenses/reviewer/review.md) delegates three independent perspectives: regressions, repository style and rules, and comprehensive review. Subagents save finding drafts; the main reviewer checks evidence, reconciles duplicates and publishes one set of results. Select it with `preset = lenses` in your INI or `prepare --preset lenses` for one run. `default` remains the default.

Reviewer policy, publication, follow-ups, discussion and fixing instructions come from the skill and cannot be replaced by a preset. The common policy requires reading repository instructions and governs delegation and evidence checking. The common protocol owns priorities, draft and finding formats, merged names, accounting for every draft and waiting for subagents before completion. Author prompts govern discussion and approved fixes separately. These instructions are shared by Claude and Codex; changing the preset changes the review criteria and method while preserving the workflow. See the [prompt responsibilities](skills/agent-review/references/commands.md).

Custom presets need only `reviewer/review.md`; other files in older presets are ignored. Keep custom presets outside the managed installation and set `preset = /absolute/path/to/your/preset`. Personal and worktree settings survive updates, and prepared reviews retain their frozen prompts.

Published filenames look like `r02-claude1-f032--p1--regression-timeout.md`: stable ID, original reviewer severity, and final draft name. A merged finding may keep one source draft name or join names with hyphens. The helper treats that name as opaque text. Human output sorts by priority and shows **crit, high, med, low, info, undef** for P0, P1, P2, P3, P4, PZ; missing severity is undef. Old filenames and the legacy severities `info` and `unclassified` remain readable.

[Skill instructions](skills/agent-review/SKILL.md) | [Commands, configuration and recovery](skills/agent-review/references/commands.md)
