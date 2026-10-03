Follow [the author workflow](../SKILL.md) for review authorization, discussion and approved fix batches. This reference describes the helper operations used by that workflow.

Managed installation and manual updating use `curl -fsSL https://github.com/sintoniastrategy/agent-review/releases/latest/download/install.sh | bash`. The installer downloads stable release assets anonymously, checks the archive checksum, retains versions in ~/.local/share/agent-review/releases and atomically switches current after a successful installation. User skill symlinks point through current. Existing unrelated skills are not overwritten. Personal settings should use worktree configure overrides or external preset paths; managed release files are replaced by new versions.

At the start of an invocation, `python3 /path/to/local-review/scripts/install_skill.py --check` checks at most once per 24 hours and prints the concrete Skill and Helper paths. Read those instructions and keep those paths for this invocation, including when current changes later. A source checkout or manual bundle copy is not updated. Failures are advisory for automatic checks and print the manual update command for a normal terminal; explicit installation returns a failure exit status. Do not bypass the user's recovery rules. The installer does not request sandbox escalation, install packages or launch a reviewer. For an isolated installation test, pass `--home /temporary/user` to install_skill.py, or pass `-s -- --home /temporary/user` to the bootstrap bash command.

The helper uses Python 3.9+ and its standard library on Linux or macOS. It needs Git, tmux, Docker by default, and a Claude subscription credentials file. The bundle manages its own pinned Claude binary. The human normally chooses the workflow in conversation; these commands are the author agent's implementation reference. Commands return programmatically generated JSON by default; prepare, start and status accept --human for concise text, and watch streams human-readable updates. Markdown export and publication acknowledgments are also plain text; agents write findings as Markdown, never JSON.

```sh
python3 /path/to/local-review/scripts/review.py --repo /path/to/worktree init --base main --task-file /path/to/task.txt
python3 /path/to/local-review/scripts/review.py --repo /path/to/worktree configure --window-name repo/worktree
python3 /path/to/local-review/scripts/review.py --repo /path/to/worktree prepare --human
python3 /path/to/local-review/scripts/review.py --repo /path/to/worktree start r01-claude1 --human
python3 /path/to/local-review/scripts/review.py --repo /path/to/worktree watch r01-claude1
```

Managed containers require Linux Docker Engine, either rootless or rootful without userns-remap. Setup inspects engine metadata and namespace security options before building an image. It uses container UID/GID 0:0 for rootless Docker and the host UID/GID for rootful Docker. Podman, including a detected Docker-compatible frontend, userns-remap and unrecognized engine or namespace metadata are rejected with an error; the helper does not change host permissions or select another runtime automatically. The selected mode is recorded as docker_mode. Preparation also saves docker_client: the absolute Docker client path, resolved endpoint, TLS flags and certificate paths, and the client environment needed for Docker configuration, SSH and proxies. Image operations, tmux startup and cleanup all use this saved connection. Switching the current Docker context or changing the tmux server environment does not redirect a prepared round. Certificate contents and Docker config files remain at their original paths; their contents are not copied or frozen. These host client settings are not passed into Claude's container environment.

The base must already exist locally. The helper never fetches it. Source snapshots preserve staged, unstaged and non-ignored untracked files without modifying the real index. Snapshot refs under `refs/agr/SESSION/INTERNAL_ROUND_ID` keep the corresponding Git trees alive. The helper never creates worktrees, branches, commits or PRs.

| Operation | Arguments | Effect |
| --- | --- | --- |
| `configure` | optional `--docker` or `--no-docker`, `--credentials-file PATH --window-name NAME --agent claude --model MODEL --effort LEVEL --preset NAME_OR_DIRECTORY --scope full/changes --reset FIELD --human` | Show effective settings; supplied fields update worktree overrides and leave other fields unchanged. Names must be ASCII without whitespace. |
| `preflight` | optional `--base REF --human` | Check Git, tmux, credentials-file presence, base/HEAD/merge-base and Docker connectivity; no installation or inference. Uses the existing cycle base when omitted. Subscription authentication is checked by managed Claude before launch. |
| `instructions` | `discuss` or `fix` | Read the selected preset's author instructions; no model launch. |
| `setup` | none | Build the image or install the separate native binary; no inference. |
| `sessions` | none | Show the current review cycle in this worktree. |
| `init` | `--base REF --task-file FILE` | Start a cycle; refuses to overwrite an existing cycle. |
| `note` | `--text-file FILE` | Record author context for subsequent passes. |
| `prepare` | optional `--agent claude --model MODEL --effort LEVEL --preset NAME_OR_DIRECTORY --scope full/changes --parallel-with REVIEWER --human` | Freeze selected settings, prompt texts, source and prior context; no inference. |
| `start` | `REVIEWER`, optional `--idle-timeout SECONDS --human` | Launch that prepared reviewer in an interactive tmux terminal. Default silence limit is 300 seconds. |
| `send` | `REVIEWER --text-file FILE` or `REVIEWER --key KEY` | Paste text and Enter, or send a supported terminal control key, to the owned open session, including after review completion. |
| `status` | optional `--human` | Show reviewer states, source versions, tmux targets, counts and open fix batch. |
| `watch` | `REVIEWER` | Observe stage updates, publication counts and terminal status until the round ends; never starts or cancels a reviewer. |
| `report` | `REVIEWER` | Read all findings, report and rechecks for this reviewer. |
| `queue` / `next` / `finding` | optional `--human`; `queue` also offers `--table` instead; `finding` requires `ID` | Read all findings, the next pending finding, or one finding. queue accepts repeated --priority to select a discussion group. |
| `import-finding` | `REVIEWER --markdown-file FILE --source-artifact NAME --reason TEXT` | Preserve a finding from a terminal round's raw output, with its provenance. |
| `assess` | `ID --priority P0..P3/info/unclassified --reason TEXT --proposal TEXT [--recommendation fix/leave/discuss]` | Record the author's judgment separately from reviewer severity. |
| `decide` | `ID [ID ...] --action fix/reject/defer/pending --reason TEXT` | Record explicit human decisions for the named IDs; revisions append history and do not undo source edits. |
| `batch-open` | `ID [ID ...]` | Open an explicitly approved batch and retain its before snapshot; there is no numeric size limit. |
| `batch-done` | `--finding ID [--finding ID ...] --summary TEXT --validation TEXT` | Record the actual completed IDs, after snapshot and diff. Supply --finding when the completed set differs from the original batch; omitted IDs remain unchanged. Without --finding, records the originally selected set. |
| `batch-cancel` | `--reason TEXT` | Record cancellation without reverting code. |
| `cancel` | `REVIEWER` | Request cancellation; observe status until terminal. |
| `close` | `REVIEWER` | Close a terminal review's session and owned pane, or clean its owned Docker container after the pane disappeared. Save an available screen snapshot and retain results. Cancel an active review first. |
| `recover` | `REVIEWER` | With no live worker, mark an active round with a disappeared pane interrupted and close its owned runtime; retain findings and never relaunch. |
| `stop` / `reopen` | `--reason TEXT` | Change cycle state without discarding pending items or launching a model. |
| `export` | optional repeated `--finding ID`, `--format json/markdown` | Export generated context or selected decisions; no network operations. |

The installed skill's `defaults.ini` supplies defaults for all worktrees using that installation. It can be edited in a manual copy; for managed releases use configure overrides and external presets to retain preferences across updates:

```ini
[review]
agent = claude
model = opus
effort = xhigh
preset = default
scope = full
```

`opus` is Claude's latest-Opus alias, not a pinned model version. To select a particular model version, use its full model name. The managed Claude CLI supports effort low, medium, high, xhigh and max. The chosen alias or name is frozen at prepare time; an alias may resolve to a newer model at launch time. The helper does not claim to record the server's resolved model version.

Selection precedence is `defaults.ini` → worktree `.agr/config.json` → arguments to `prepare`. Config stores only explicit overrides; inherited defaults remain inherited. `configure` with no arguments shows effective settings and the stored overrides without rewriting the config. `configure --human` shows the same selection in plain text. To change only the effort, use `configure --effort high`; credentials, runtime, model and preset remain unchanged. Use `configure --reset model --reset effort` to remove those overrides and inherit the installed defaults again. Reset also accepts agent, preset, scope, runtime, credentials_file and window_name. A field cannot be set and reset in the same command. Invalid selections fail before runtime setup or round creation.

Each preset is a directory containing these nonempty UTF-8 files:

| File | Purpose |
| --- | --- |
| `reviewer/review.md` | Scope and review criteria. |
| `reviewer/policy.md` | Autonomy, internet, subagents, available checks and progress. |
| `reviewer/followup.md` | Prior decisions, fix records, selected repeat scope and explicit rechecks. |
| `author/discuss.md` | Proportional assessment, default discussion sequence, tables, group decisions and revisions. |
| `author/fix.md` | Batch guidelines, actual completion, validation and optional authorized delegation. |

Copy the default preset directory to customize it. A bare name selects presets/NAME in the installed skill. A path such as ./review-presets/security is relative to the reviewed worktree. The selected files replace their corresponding defaults. The skill always adds prompts/reviewer/protocol.md separately; changing reviewer policy does not require copying publication mechanics. Prompt text is ordinary message content, without template interpolation. Claude's built-in system prompt and runtime tool restrictions remain separate.

```sh
python3 /path/to/local-review/scripts/review.py --repo /path/to/worktree configure --preset ./review-presets/security --human
python3 /path/to/local-review/scripts/review.py --repo /path/to/worktree prepare --model sonnet --effort high --scope full --human
python3 /path/to/local-review/scripts/review.py --repo /path/to/worktree instructions discuss
```

Scope full reviews the complete branch change. Scope changes reviews every source change since the last successfully completed review, including work outside recorded fix batches, with surrounding context as needed. The first review requires full. Failed attempts never advance the changes baseline. Old findings from failed attempts still appear in history. The helper does not fetch or guess the base.

Prepare copies selected reviewer texts to input/, adds followup.md when there is prior context, and saves prompt.md. It states the reviewer ID explicitly and writes the same ID to input/reviewer.txt. It records scope, model, effort, preset and the previous completed review. Later edits apply only to future preparations. Parallel reviewers share frozen source and prior history but can select different scopes and presets. Only Claude launching is implemented.

Reviewer addresses are `r03-claude1`: pass 3, Claude slot 1. Every command that targets a reviewer, including --parallel-with, takes that address. Internal numeric round IDs remain bookkeeping fields in JSON, not command selectors. Findings use `r03-claude1-f015` in both their metadata and filenames. Passes have at least two digits and findings at least three; larger numbers are retained without truncation. Slots have no zero padding. Prompts, models, effort and lenses are not components of identity. Imported findings use the same f-number sequence as published findings and retain their import provenance.

Use queue --human, next --human or finding ID --human for discussion. With one reviewer, the header names that reviewer and rows show R3-F15 (P:Low), using the actual priority. Mixed reviewers use R3-Claude1-F15. These are presentations of existing stable IDs, not table row numbers. The full ID remains available from finding ID --human and the default JSON output.

Use queue --table to show every finding in numeric pass, reviewer and finding order. Columns show the finding, current priority, stored title, saved author recommendation and reason, current human decision, recorded fix, latest reviewer verdict and its source pass. A single reviewer is named above the table; mixed reviewers keep their agent and slot in the finding label. Long titles wrap without truncation; the helper does not generate or translate summaries. The title column adapts to terminal width, with a wide layout when output is redirected. Missing rechecks show Not rechecked and no check pass. This uses the same independent decision and latest-recheck meanings as status --human. The existing queue JSON and --human list retain their priority ordering.

Use status --human for a cycle-wide finding summary followed by the latest pass's reviewer states. The first block counts current human decisions: not discussed, approved to fix, deferred and rejected. Recorded fixes are counted independently, so changing a decision never erases a completed fix. Priority counts are shown separately. The second independently counts each finding's latest recorded reviewer recheck: not rechecked, confirmed resolved, still present, changed or uncertain. Recheck counts show their source pass numbers, not internal round IDs. Each finding counts once in each block. A later recheck replaces the earlier verdict in this summary, including when parallel reviewers disagree; the full evidence remains in report and finding. An omitted recheck retains the previous verdict and its original pass. These verdicts describe the recorded snapshots; they do not verify subsequent source edits or change human decisions. Default JSON status remains unchanged.

Use prepare --human, start REVIEWER --human and watch REVIEWER when showing a run to a human. Watch prints state, stage or publication changes, and reports a minute without new stage/publication events explicitly. It shows the last terminal-output time separately: terminal redraws are not proof of review progress. Ctrl-C stops only the observer. A completed review exits watch successfully while leaving its Claude session open; failed, stalled or interrupted rounds produce a nonzero exit status. Human status shows all reviewers in the latest pass.

The reviewer writes its latest short stage message to output/progress.txt and repeats it in the terminal. This optional plain-text file is a replaceable display hint, not a finding or completion signal, and is excluded from the publication manifest. Only recorded round status determines completion. The observer reads a bounded amount of text and does not follow a progress-file symlink. An absent update is shown as absent; the helper does not infer what the model is thinking. The idle observer takes one metadata snapshot per regular file, ignores symlinks, and tolerates a progress file disappearing or being replaced between observations.

One worktree has one review cycle. Files live in its ignored `.agr` directory:

```text
.agr/
  config.json
  session.json
  decisions/
    0001-decision-r01-claude1-f001.md
    0002-assessment-r01-claude1-f001.md
  fixes/
    0003.diff
  rounds/
    r01-claude1/
      status.json
      prompt.md
      input/
        review.md
        policy.md
        protocol.md
        followup.md
        reviewer.txt
        history.md
        fixes/
      output/
        drafts/
        findings/
        checks/
          r01-claude1-f001.md
        report.md
        progress.txt
        complete.json
      terminal.log
      screen.txt
      launch.json
    r01-claude2/
  .cache/
```

Findings and rechecks are ordinary Markdown with a few optional one-line headers. IDs come from filenames; round and reviewer identity come from the containing directory. Findings may include Title, Severity, Path, Line, Start-Line, Side and Related-To. Only evidence is mandatory. Note:, Summary: and URLs at the start of plain text are not treated as service headers. Line and Start-Line are separate positive integers. At and Draft are added by the publisher for chronology and repeat publication; no internal round, _text or draft hash is required. Explicit author imports retain their source artifact and import reason in Imported-By-Author metadata.

Author decisions, assessments and fix records remain individual append-only Markdown documents with a small generated typed header and free-form reason or summary. Decision and assessment filenames include the existing finding ID. A changed decision is a new record, including during an open batch. Decisions, completed edits and rechecks are separate facts. The helper checks that referenced IDs exist but cannot prove that the human agreed or that a fix is correct.

Batch size is a prompt guideline. batch-open retains a before snapshot; batch-done records only the actual selected completed IDs and retains an after snapshot and fixes/SEQUENCE.diff. Git refs keep both trees alive. A human can revise decisions or request a new review while a batch is open; the author reconciles the remaining work and the recorded completion. Only one batch is open at a time. Source changes still require active reviewers to finish or be cancelled. Several completed batches can precede one new review. The next preparation copies batch diffs into input/fixes.

Findings are normally published incrementally from output/drafts. The publisher assigns a stable ID under the reviewer's output lock and writes atomically without replacing an existing result. Retrying an unchanged draft returns its saved finding. Author journal operations use .agr/.lock. Locks cover only short filesystem operations, not inference; this assumes local flock and atomic file semantics. Drafts and raw output remain available when interrupted.

Inside the reviewer, the exact command is supplied in the prompt:

```sh
python3 /opt/agr/scripts/review.py publish /absolute/output finding --file /absolute/output/drafts/issue-001.md
python3 /opt/agr/scripts/review.py publish /absolute/output check --file /absolute/output/checks/r01-claude1-f001.md
python3 /opt/agr/scripts/review.py publish /absolute/output finish
```

A recheck can be written directly to output/checks/ORIGINAL_FINDING_ID.md with Status: resolved/still_present/changed/uncertain, a blank line and evidence. No draft or helper call is mandatory for this file. Optional publish check validates the final file; publishing a draft with Finding and Status headers produces the same canonical path. A short report is plain output/report.md; it can also be published from a draft. Its purpose is coverage and limitations, not a duplicate of all findings.

Finish requires a nonempty report and records the round and finish time in complete.json. It does not hash publications, certify completeness or block on unpublished drafts. The observer recognizes this explicit signal; a quiet terminal or process exit is not completion. The author reads remaining artifacts, reconciles their contents and asks the human when needed. Malformed records produce a specific path and error; there is no tolerant journal mode, silent omission or automatic repair. A missing recheck does not resolve a finding. Runtime configuration and process state remain small machine-generated JSON files.

Docker starts with `--interactive --tty` inside the tmux pane. Claude inherits the terminal directly: no `-p`, SDK, stream-JSON protocol or stdout pipe. Tmux records the display separately using pipe-pane. The helper waits for Claude's input prompt and submits the assignment with bracketed paste plus Enter. Subsequent input can be sent through `send` or by attaching normally. Outside tmux, a detached session is created and the attach command is returned. Window names look like `agr@repo/worktree-r01-claude1`; automatic renaming and application renaming are disabled. Before starting a new pass, the helper closes only owned terminal sessions from earlier passes, waits for their runtime cleanup, then removes their panes. A missing pane does not prove the container stopped: an unfinished Docker cleanup still inspects the recorded container name and session label, then removes the matching container by its immutable Docker ID. A surviving worker or uncertain ownership blocks cleanup. If the interrupted review is still marked active, recover REVIEWER first records interruption and closes its owned runtime. A surviving attached Docker client does not prevent removal of its verified container; no process is killed by a recorded PID. Native recovery still refuses a surviving reviewer process. If cleanup fails after interruption is recorded, that interrupted status and the cleanup error remain available for inspection. Foreign panes and containers remain untouched. Other reviewers in the same pass remain open. The helper saves the visible screen and available scrollback to screen.txt before explicitly closing a pane; terminal.log and published files remain. Deleting `.agr` also removes ownership records; the helper will not guess which old windows it may close.

The image contains the pinned Claude release and helper. The source worktree and linked Git metadata are read-only. Only this reviewer's output and the single credentials file are writable bind mounts. Credentials are mounted at /run/agr/credentials.json, outside the temporary home. The runtime user creates HOME and .claude with mode 0700 and links the credentials file into .claude; Docker-created mount parents do not determine home ownership. The container has a read-only root filesystem, no extra capabilities and no Docker socket. User launchers, settings, API-key environment variables and shell profiles are not used. Hooks are disabled once through --settings; --strict-mcp-config prevents automatic MCP configuration loading. The pinned release does not update itself.

Claude retains its normal system prompt, default tool set, skills and memory behaviour with bypassPermissions. There is no general tool allowlist or separate Chrome override. The launcher passes --disallowedTools AskUserQuestion,EnterPlanMode,ExitPlanMode to exclude tools that require questions or plan approval. Subagents remain technically available; the default workflow preset requests an independent review, while a different review prompt may allow delegation. The prompt requires continuing independent checks without clarification and reporting assumptions, uncertainty and coverage limitations before finishing. These instructions and tool exclusions do not guarantee the model will never ask a question in ordinary text; the existing idle watchdog still applies before completion. Source fixes, tests, dependency installation and extra reviewers are outside the current prompt's task. Focused documentation access remains available, and follow-up questions after completion still work normally.

For explicitly chosen direct execution in an already isolated environment, `configure --no-docker` installs the pinned binary under `.agr/.cache/runtime` and creates an ephemeral clean home. Direct mode is not a filesystem sandbox. Subscription authentication is checked in both modes before inference; there is no API-key fallback. Neither TTY presence nor subscription authentication proves the provider's billing treatment.

Keep source unchanged until review status becomes terminal. A successful report sets status to completed and finished_at immediately, while session_open remains true. The worker keeps Claude, its container and temporary home alive for reading and follow-up input until close REVIEWER, a normal CLI exit, or the next pass. Closing sets session_open to false and closed_at without changing the completed report or its completion time. Cleanup success is recorded separately as cleanup_complete. Errors are retained in session_error and block the next pass even when the old pane is gone; a false session_open alone does not prove runtime cleanup succeeded. Stopping the client and cleaning the container are separate attempts, so failure to stop the client does not skip container cleanup. After inspection and the user's recovery approval, explicitly call close REVIEWER to retry an owned Docker cleanup. Success clears session_error and retains its prior value as previous_session_error without changing findings, reports or the original review completion time. Automatic startup never retries a previously recorded cleanup error. Old rounds without saved Docker connection metadata cannot be launched or have uncertain cleanup resolved by guessing the current connection; previously confirmed closed sessions remain closed. The idle watchdog applies only before report completion. After completion, source edits do not invalidate the saved review; questions about that review should refer to its recorded snapshot. Source comparison before and after each run detects stale results. Dirty submodule contents are rejected because the parent snapshot cannot retain them. Interrupted and failed runs retain all published findings, drafts and terminal output and never resume automatically. A reviewer startup that does not reach the input prompt within 60 seconds fails with the retained terminal log. Follow the user's recovery approvals before retrying or changing launch behavior.

Scoped verification, using fake reviewer processes and isolated tmux sockets, without model calls:

```sh
python3 -m unittest tests.test_store tests.test_source tests.test_reporting tests.test_runner tests.test_tmux tests.test_cli tests.test_runtime tests.test_progress tests.test_configuration tests.test_flow tests.test_names
```

Installer and release packaging checks are scoped separately: `python3 -m unittest tests.test_install`. They use temporary homes and local download fixtures, never the user's actual skill directories or a model. Build release assets with `python3 scripts/build_release.py v0.1.0 /new/output/directory`; it creates agent-review.tar.gz, release.json, install.sh and install_skill.py without publishing. Upload these assets together when creating a stable GitHub Release, keeping a published version immutable. The SHA-256 checks transfer integrity against the release manifest; it is not an independent signature.

The Docker home check is opt-in and uses an existing local image built from the current skill. It reads the helper and release manifest from that image:

```sh
AGR_TEST_DOCKER_IMAGE=sha256:YOUR_LOCAL_IMAGE_ID python3 -m unittest tests.test_runtime_docker
```

It checks private home ownership, atomic state writes, onboarding state and the credential link with container users 0 and 1000. Only the test probe is staged as a readable disposable copy; the helper and release manifest are not replaced, so the check covers their image permissions. Original worktree permissions are preserved. It uses disposable credential and output fixtures accessible to both test users, disables networking, never pulls an image, and intercepts authentication and model execution. On a rootless daemon this validates the unprivileged home setup but does not exercise a full rootful run, real authentication or the model's effective tool list.

A separate, explicitly authorized release check used a clean Ubuntu 24.04 VM with rootful Docker and a fresh skill installation. Claude Sonnet at medium effort completed an interactive review and found the planted regression. Closing removed the owned pane and container, preserving source and published results. A separate dummy-credential probe verified writable credential-file persistence. Real OAuth credentials did not refresh during that run, so provider-driven token refresh remains unverified. A full macOS or Docker Desktop review has not been validated. Live checks require explicit approval; the scoped tests above do not launch a model.
