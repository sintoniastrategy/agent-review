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

1. **Review.** A fresh interactive Claude session runs in tmux and reviews your branch changes, including uncommitted work, against your chosen base.
2. **Discuss.** Your existing coding agent keeps the task context. It walks through critical/high findings individually and presents medium/low findings in tables with recommendations.
3. **Choose and fix.** Approve, skip or defer findings. Change your mind whenever needed. Your coding agent fixes the approved items in suitable batches.
4. **Check again.** When you request another pass, the reviewer gets previous findings, your decisions and the fix diffs. Recorded fixes and reviewer confirmations remain separate.

Stop whenever you've fixed enough. Findings, decisions and rechecks stay in readable Markdown files under `.agr/`. No PR, GitHub Actions, MCP server or database required. Orchestration runs locally; Claude inference still uses Anthropic's service.

## Compared with other review tools

A comparison of documented workflows, checked October 2026:

| Tool | What it offers |
| --- | --- |
| [Codex built-in `/review`](https://learn.chatgpt.com/docs/codex/cli) | Prioritized findings for a branch, commit or uncommitted changes, without modifying your working tree. |
| [Anthropic's Code Review plugin](https://claude.com/marketplace/plugins/code-review) | Parallel specialist reviews with confidence filtering and findings posted to GitHub PRs. |
| [Garry Tan's gstack `/review`](https://github.com/garrytan/gstack/blob/main/review/SKILL.md) | Pre-landing review with specialist passes, automatic or approved fixes, repeat reviews and saved skip decisions. |
| [obra's Superpowers](https://github.com/obra/superpowers/tree/main/skills/requesting-code-review) | Review subagents and feedback-handling skills within a broader planning, TDD and development workflow. |
| [Everything Claude Code / ECC](https://github.com/affaan-m/ECC/tree/main/commands) | Local and PR review checklists, specialist review agents and orchestrated verification within a large toolkit. |
| **SST Agent Review** | A focused review/fix loop: your existing author session, a separate interactive reviewer, human decisions and a local journal across passes. |

Choose SST when you want to work through findings with the agent that wrote the code and retain the reasoning behind every fix or skip.

## Installation

The command above installs the latest release into `~/.local/share/sst-agent-review`, with skill symlinks in `~/.agents/skills/sst-agent-review` and `~/.claude/skills/sst-agent-review`.

You'll need **curl, Python 3.9+, Git, tmux and an existing Claude subscription login**. The skill manages its own pinned Claude binary and clean configuration. Prerequisites are checked, not installed automatically.

- **Docker is the default:** local Linux Docker Engine, rootless or rootful, with source mounted read-only. Podman and userns-remap are unsupported.
- **Native mode is optional:** ask for it on Linux or macOS. Native macOS uses your default Claude Code Keychain login; Docker and native Linux use a credentials file. Native sandboxing is best effort and may fall back to your user permissions. Full macOS validation is still pending.

Updates are checked on skill invocation at most once daily. New invocations use the updated version; running workflows retain theirs. If updating is blocked, the skill explains how to run the install command manually.

**Manual installation:** from a checkout, copy the entire bundle into your agent's skills directory, using a destination that does not already exist:

```sh
mkdir -p ~/.agents/skills
cp -R skills/agent-review ~/.agents/skills/sst-agent-review
```

For Claude Code, use `~/.claude/skills` instead. Manual copies do not auto-update.

## Make it yours

Defaults: **Claude Opus, xhigh effort, full review**. Just ask for Sonnet, a different effort, a review of changes since the last pass, or a different discussion style. Claude is currently the only launched reviewer; Codex can be your author agent.

[Prompt presets](skills/agent-review/presets/default) separate review criteria, reviewer policy, follow-ups, discussion and fixes. Personal and worktree overrides survive managed updates.

[Skill instructions](skills/agent-review/SKILL.md) | [Commands, configuration and recovery](skills/agent-review/references/commands.md)
