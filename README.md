# SST Local Review

Review code locally with a separate interactive Claude session while keeping implementation and discussion in your existing author agent. The author discusses critical findings individually, presents medium and low priorities in tables with recommendations, records revisable decisions, and fixes the selected items in suitable batches. The skill explains each stage and recommends a default next step; users can change the flow in conversation. You decide when to request another pass or stop.

The skill bundles Python helpers for tmux, an isolated Claude runtime, and a local review journal. It needs no GitHub workflow, PR, MCP server or database. Claude is currently the only implemented reviewer; your author session can be Codex or another agent that can follow the skill and run its helpers.

## Requirements and installation

On Linux or macOS, install or update with one command:

```sh
curl -fsSL https://github.com/sintoniastrategy/agent-review/releases/latest/download/install.sh | bash
```

The installer needs `curl` and Python 3.9+. It downloads the latest stable release, verifies the archive's SHA-256, and stores it under `~/.local/share/agent-review/releases/VERSION`. The `current` symlink selects the installed version. Skill symlinks are created in `~/.agents/skills/sst-local-review` for [Codex](https://developers.openai.com/codex/skills/) and `~/.claude/skills/sst-local-review` for [Claude Code](https://code.claude.com/docs/en/skills). An existing unrelated skill is left untouched and reported. Start a new author-agent session after the first installation and ask it to use SST Local Review (`sst-local-review`). Old installer-owned local-review links are migrated; unrelated skills are preserved. The internal archive directory stays `local-review` so existing updaters can read new releases.

At skill entry, the author checks for updates at most once per 24 hours. After a successful update it reads the new instructions and pins the concrete version path for the invocation. Already running author workflows and reviewers keep their previous version; installed versions are retained. There is no system service, timer, API token or `gh` requirement. A sandbox or network failure is reported with the manual command above; it does not remove the installed version. Running the installation command manually bypasses the daily check interval.

Treat managed release files as versioned software. Keep personal defaults with `configure --global` in `~/.local/share/agent-review/config.json`, worktree preferences with `configure` overrides and custom presets outside the release directory so upgrades do not replace your preferences. The installer does not install Docker, tmux or Claude credentials. The skill checks these before review:

- Python 3.9+, Git and tmux on the host. The helpers use only Python's standard library.
- By default, a local Linux Docker Engine, rootless or rootful without userns-remap, accessible to your user. Podman and userns-remap are not supported.
- Claude subscription authentication: native macOS uses the default Claude Code login in Keychain. Docker and native Linux use an existing credentials file, normally `~/.claude/.credentials.json`. The helper does not perform an interactive login. Docker has no Keychain bridge.
- Native mode can be selected explicitly without Docker. It uses Claude's built-in Bash sandbox when available: Seatbelt on macOS, bubblewrap and socat on Linux. Missing sandbox support produces a warning and allows commands with your user permissions; dependencies are never installed automatically.
- Network access to build the runtime and use Claude. The first preparation downloads a pinned, checksum-verified Claude CLI; no existing Claude executable, aliases, pip packages or compiler are needed.

For an explicitly manual installation without automatic updates, copy the **whole** [local-review bundle](skills/local-review), including its presets and runtime files, into the skills directory supported by your author agent. From this repository, replace the destination below with its absolute path:

```sh
AGR_SKILLS_DIR=/absolute/path/to/your/agents/skills
mkdir -p "$AGR_SKILLS_DIR"
cp -R skills/local-review "$AGR_SKILLS_DIR/sst-local-review"
```

For a first installation, use a destination without an existing `sst-local-review` folder. You can also give your author agent the absolute path to [SKILL.md](skills/local-review/SKILL.md) in this checkout and ask it to follow those instructions. The helper path is relative to the installed skill, not to the repository being reviewed.

Then ask the author agent, for example:

> Use SST Local Review for this worktree against the local main branch. The task was to reject empty input while preserving fractional averages. Run Claude Sonnet with medium effort. Use the default discussion flow: critical findings individually, then tables for medium and low priorities. Explain your recommendations and record my choices.

Use your actual base and task description. The author follows your repository's approval rules. A request for one review does not authorize fixes, extra reviewers or repeat passes.

## First run through the helper

These commands expose what the skill does. The author normally runs them for you. Replace the paths, choose an existing local base, and write a task file describing the intended change and scope. Keep that file outside the reviewed source tree.

```sh
AGR_SKILL=/absolute/path/to/sst-local-review
AGR_REPO=/absolute/path/to/your/worktree
AGR_TASK=/absolute/path/to/task.txt
AGR_BASE=main
review() { python3 "$AGR_SKILL/scripts/review.py" --repo "$AGR_REPO" "$@"; }
review configure --human
review sessions
```

For a new cycle:

```sh
review configure --docker --window-name repo/worktree
review preflight --base "$AGR_BASE" --human
review init --base "$AGR_BASE" --task-file "$AGR_TASK"
review prepare --human
```

If credentials are elsewhere, set `review configure --credentials-file /absolute/path/to/credentials.json` before preparation. `setup` can prepare the runtime separately; `prepare` also does this. Neither starts inference. An existing cycle is continued with `status --human` and `queue --table`; do not initialize it again.

Preparation freezes the model, prompt, previous decisions and source snapshot. It covers changes from the branch's merge base with the selected base, including staged, unstaged and non-ignored untracked files, without changing your real Git index. The base must already exist locally; the helper does not fetch it. Keep source unchanged until the review finishes.

Use the reviewer address printed by preparation. For a first pass it is normally `r01-claude1`:

```sh
review start r01-claude1 --human
review watch r01-claude1
```

`start` launches the real interactive Claude CLI in tmux and sends the prompt through terminal input. The window is named `agr@repo/worktree-r01-claude1`. Your author session need not be in tmux: the helper creates a detached session and prints an attach command. `watch` reports saved stages, publication counts and terminal activity; Ctrl-C stops watching without cancelling Claude.

## Defaults and prompt presets

Selection precedence, from lowest to highest:

| Location | Applies to |
| --- | --- |
| Installed [defaults.ini](skills/local-review/defaults.ini) | All worktrees using that skill installation |
| Personal `~/.local/share/agent-review/config.json`, edited through `configure --global` | All future preparations for this user |
| Worktree `.agr/config.json`, edited through `configure` | Future preparations in this worktree |
| Flags on `prepare` | That reviewer only |

The shipped defaults are `agent = claude`, `model = opus`, `effort = xhigh`, `preset = default`, `scope = full`. `opus` selects the latest Opus alias; use a full model name to request a specific version. Supported efforts are `low`, `medium`, `high`, `xhigh`, and `max`.

Set personal defaults, including runtime, with `configure --global`. This also works outside a Git repository and survives skill updates. For example, `review configure --global --model sonnet --effort medium`. Omit `--global` to set worktree overrides:

```sh
review configure --model sonnet --effort medium --human
```

Or choose settings for one new preparation:

```sh
review prepare --model sonnet --effort medium --preset default --human
```

Inspect settings with `configure --human`; restore inherited model and effort with `configure --reset model --reset effort`. Unspecified fields stay unchanged. Add `--global` to inspect or reset personal defaults. Worktree settings override personal defaults; resets remove only the selected layer's override. Editing defaults or overrides does not change already prepared rounds.

Each preset separates reviewer and author instructions:

| File | Controls |
| --- | --- |
| [reviewer/review.md](skills/local-review/presets/default/reviewer/review.md) | Review criteria and findings to include |
| [reviewer/policy.md](skills/local-review/presets/default/reviewer/policy.md) | Autonomy, internet, subagents and checks |
| [reviewer/followup.md](skills/local-review/presets/default/reviewer/followup.md) | Prior context and repeated review |
| [author/discuss.md](skills/local-review/presets/default/author/discuss.md) | Proportional assessment, tables, group choices and revisions |
| [author/fix.md](skills/local-review/presets/default/author/fix.md) | Fix batches, validation and optional authorized delegation |

The skill adds its [file protocol](skills/local-review/prompts/reviewer/protocol.md) separately. Copy the default preset directory to customize its texts. A bare name selects presets/NAME; a custom path is relative to the reviewed worktree when not absolute. Users can also change discussion and delegation preferences in conversation without editing files. The author loads effective instructions with instructions discuss or instructions fix.

```sh
review configure --preset ./review-presets/security --human
```

These are ordinary message instructions. Claude's system prompt and the runtime's tool restrictions remain separate. The default policy requests independent review. Prompts and lenses do not change IDs.

Every preparation saves the selected files under its `input/` directory and the assembled message as `prompt.md`. Later edits affect only future preparations. For explicitly requested parallel Claude reviewers, prepare the additional reviewer with `--parallel-with r01-claude1` before starting them; each gets its own slot and output directory. `--agent codex` is not implemented yet.

## Discuss, fix and review again

After completion, read the report and all findings, including coverage limitations:

```sh
review report r01-claude1
review status --human
review queue --table
review next --human
review finding r01-claude1-f001 --human
```

`status --human` separates human decisions and recorded fixes from reviewer rechecks. `queue --table` shows every finding in pass/reviewer/finding order, with priority, stored title, author recommendation and reason, human decision, recorded fix, latest recheck and its pass. Use queue --priority P2 --table to inspect one priority group. Python reads the title from the finding; it does not generate a summary. `queue --human` lists findings by priority.

The default flow discusses critical/high findings individually, fixes the selected critical items, and proposes an explicitly requested recheck before lower priorities. Medium findings first appear in one table; detailed discussion focuses on points needing your attention. Low findings appear in a table with usually two or three worthwhile fixes proposed. Reviewer severity is the starting point; the author checks context proportionately instead of repeating every investigation. Group choices apply only to the displayed group, and you can revise decisions at any time. For an illustrative approved fix:

```sh
review assess r01-claude1-f001 --priority P1 --reason "Floor division loses fractional averages" --proposal "Use true division" --recommendation fix
review decide r01-claude1-f001 --action fix --reason "Approved: preserve fractional results"
```

Use `reject` for an explicit won't-fix decision, `defer` to postpone, or `pending` when undecided. Record the reason, including pre-existing behavior or scope exclusions. Keep every finding even if you stop before discussing it.

When you request implementation, the author opens a batch of approved IDs, usually a few straightforward fixes or one complex issue. Larger agreed batches are allowed:

```sh
review batch-open r01-claude1-f001
```

The author then edits source and runs the repository's agreed checks. Only after completing that work, record what actually changed and the actual validation result:

```sh
review batch-done --finding r01-claude1-f001 --summary "Replaced floor division with true division" --validation "Average tests passed, including fractional input"
```

The helper retains before/after snapshots and the batch diff; batch-done does not implement or test a fix. Supply the IDs actually completed, especially after changed decisions. Work stays in the author session by default; delegated fixes require explicit authorization and integration by the author. `batch-cancel --reason TEXT` retains any partial source changes. Additional context can be recorded with `note --text-file FILE`.

On your explicit request for another review, call `prepare`, then `start` and `watch` with the **new address returned by preparation**. The next reviewer receives source diffs, batch diffs and prior findings, decisions, reasons, fixes and reports. Scope full is the default; prepare --scope changes reviews all changes since the last successfully completed review. Several fix batches may precede one review. Failed attempts do not advance this baseline, and the first run needs full scope. A recorded fix is separate from a reviewer's `resolved` verdict; omission in a new report never resolves a finding.

Completed reviews leave their Claude session open for follow-up questions. Close it when finished:

```sh
review close r01-claude1
```

Starting the next pass closes owned sessions from earlier passes and retains parallel peers in the same pass. Close preserves publications and logs. To stop the cycle while retaining outstanding items, use `stop --reason TEXT`; `reopen --reason TEXT` reopens it without launching a model. Stop is a journal action; use `cancel` for an active reviewer and `close` for its terminal session.

## Files and recovery

One worktree has one review cycle, with as many passes as needed. Its `.agr/` directory ignores itself in Git:

| Path under `.agr/` | Contents |
| --- | --- |
| `config.json`, `session.json` | Worktree overrides and cycle metadata |
| `decisions/` | Individual Markdown assessments, decisions, fix records and notes; per-finding filenames include the stable ID |
| `fixes/` | Diffs of completed fix batches |
| `rounds/r01-claude1/prompt.md`, `input/` | Frozen prompt, source diffs and previous history |
| `rounds/r01-claude1/output/` | Drafts, individual findings, rechecks, report and completion marker |
| `rounds/r01-claude1/status.json`, `terminal.log`, `screen.txt` | Runtime state, terminal output and screen saved at close |
| `.cache/` | Disposable runtime and preparation files |

Full finding IDs such as `r03-claude1-f015` identify pass, reviewer slot and finding. Human discussion may shorten this to `R3-F15` when the reviewer is clear. Files always retain the full ID. Findings use simple headers and free-form Markdown. Rechecks are checks/ORIGINAL_FINDING_ID.md with Status and an explanation; reports are plain Markdown. Human decisions remain separate durable records; locks and atomic publication protect concurrent writers. Small JSON files hold machine-generated metadata. Use helper commands instead of editing published records in place.

If a run fails or becomes uncertain, retain its artifacts and follow your recovery approval rules before retrying or changing the environment:

| Situation | Supported action |
| --- | --- |
| Stopped watching | Inspect `status --human`, or run `watch REVIEWER` again; the reviewer keeps running. |
| Need to stop an active review | `cancel REVIEWER`, observe terminal status, then `close REVIEWER`. |
| Source changed after preparation | Cancel the stale preparation and prepare a fresh round. |
| Pane disappeared while the round is active | After inspection and recovery approval, `recover REVIEWER` records interruption and cleans the owned runtime; it refuses a surviving worker and never restarts Claude. |
| Terminal round has a cleanup error or orphan container | After inspection and recovery approval, `close REVIEWER` retries owned cleanup. A saved cleanup error blocks new launches. |

A quiet or exited terminal is not a successful review: the reviewer must explicitly signal completion. This does not certify coverage; remaining drafts are reconciled by the author and do not block finishing. Active reviews with no terminal output or publication activity for five minutes become stalled. Startup has a separate 60-second timeout. Failed runs retain findings, drafts and logs; there is no automatic retry. Do not delete `.agr/` to repair a runtime: its ownership records are needed for safe cleanup. See the [command reference](skills/local-review/references/commands.md) for detailed lifecycle behavior.

## Isolation and verification limits

Docker gives Claude a fresh home and the helper's own pinned binary. Source, Git metadata and author decisions are mounted read-only; its output and the single credentials file are writable. User wrappers, settings, hooks and automatic MCP loading are disabled. Claude uses `bypassPermissions`; tools for human questions and plan approval are excluded. Network access remains enabled for focused documentation checks. This is not an internet or credential-exfiltration sandbox.

`configure --no-docker` selects a managed native binary with a fresh temporary home and configuration. It does not load your Claude settings, hooks, MCP configuration or shell wrappers. Normal research tools and Bash are allowed. Claude's built-in Bash sandbox is requested when its prerequisites exist; missing support or initialization failure warns and continues unsandboxed. File-tool reads are restricted to the worktree and helper/Git directories. Native mode is not whole-process or read-only isolation: it may write source, and unsandboxed commands have your user permissions. Docker connection settings are fixed at preparation so launch and cleanup use the same endpoint.

On macOS, choose `configure --no-docker --auth keychain` to use the default Claude Code Keychain login independently of the clean profile. OAuth refresh is handled by Claude directly in that store; the helper does not export Keychain tokens. First access may trigger a macOS Keychain approval. `--auth auto` selects Keychain for native macOS unless a credentials file is explicitly configured, and a file for other runtimes. `--auth file --credentials-file PATH` explicitly selects a file. Custom Keychain profiles are not discovered. Docker Desktop and a full macOS review, including actual Keychain access and Seatbelt enforcement, still require validation on a Mac.

A clean Ubuntu 24.04 VM with rootful Docker passed an interactive Sonnet/medium review, finding the planted regression and publishing results. Closing removed its owned pane and container while preserving results and source. A separate dummy-credential check verified writable credential-file persistence. **A real provider OAuth token refresh has not yet been verified.** Interactive mode and subscription authentication do not prove the provider's billing treatment.

The [author instructions](skills/local-review/SKILL.md) define the interaction process. The [command reference](skills/local-review/references/commands.md) covers every command, publication details and scoped verification. Optional `export --format markdown` prepares local text for a PR; publishing it remains a separate, explicitly authorized action.

## macOS smoke check

After installing this version, give your author agent the worktree path and an existing local base, then ask:

> Use SST Local Review in native mode with Keychain, Claude Sonnet and medium effort. Check prerequisites, then run one review against my selected base.

Before starting, the skill should show native mode, Keychain authentication and the sandbox warning or requested protection. The first Keychain access may ask for OS approval. Confirm that the reviewer reaches its prompt without Claude login, trust or tool-permission questions; publishes findings and a report; and that `close REVIEWER` removes its owned tmux pane and temporary profile while preserving results. Check Claude `/status` for subscription authentication. Do not print credentials. This real macOS check is user-run; Linux fixtures do not establish Keychain or Seatbelt behavior.
