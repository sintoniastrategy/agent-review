import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil

from . import ReviewError, now, read_json
from .prompts import prepare, previous_review
from .source import resolve, root, same_source, snapshot
from .store import ACTIVE, Journal
from . import tmux
from . import runtime
from . import progress
from . import configuration
from . import priorities
from .names import round_name


def parser():
    result = argparse.ArgumentParser(description="SST Agent Review helper; Python 3.9+, Git, tmux and managed reviewers")
    result.add_argument("--repo", default=".")
    result.add_argument("--session")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("setup")
    check = commands.add_parser('preflight')
    check.add_argument('--base')
    check.add_argument('--human', action='store_true')
    instructions = commands.add_parser('instructions')
    instructions.add_argument('phase', choices=('discuss', 'fix'))
    init = commands.add_parser("init")
    init.add_argument("--base", required=True)
    init.add_argument("--task-file", type=Path, required=True)
    commands.add_parser("sessions")
    review = commands.add_parser("prepare")
    review.add_argument('--human', action='store_true')
    review.add_argument("--parallel-with", metavar='REVIEWER')
    review.add_argument('--agent', choices=configuration.AGENTS)
    review.add_argument('--model')
    review.add_argument('--effort', choices=configuration.CODEX_EFFORTS)
    review.add_argument('--preset', metavar='NAME_OR_DIRECTORY')
    review.add_argument('--scope', choices=configuration.SCOPES)
    start = commands.add_parser("start")
    start.add_argument('--human', action='store_true')
    start.add_argument("round", metavar='REVIEWER')
    start.add_argument("--idle-timeout", type=float, default=300)
    for name in ("cancel", "close", "recover", "report"):
        subcommand = commands.add_parser(name)
        subcommand.add_argument("round", metavar='REVIEWER')
    send = commands.add_parser('send')
    send.add_argument('round', metavar='REVIEWER')
    content = send.add_mutually_exclusive_group(required=True)
    content.add_argument('--text-file', type=Path)
    content.add_argument('--key', choices=('Enter', 'Escape', 'C-c', 'C-d', 'Up', 'Down'))
    status = commands.add_parser("status")
    status.add_argument('--human', action='store_true')
    watch = commands.add_parser('watch')
    watch.add_argument('round', metavar='REVIEWER')
    commands.add_parser("next").add_argument('--human', action='store_true')
    queue_command = commands.add_parser('queue')
    queue_command.add_argument('--priority', action='append', choices=priorities.VALUES)
    queue = queue_command.add_mutually_exclusive_group()
    queue.add_argument('--human', action='store_true')
    queue.add_argument('--table', action='store_true')
    finding = commands.add_parser("finding")
    finding.add_argument("id")
    finding.add_argument('--human', action='store_true')
    assess = commands.add_parser("assess")
    assess.add_argument("id")
    assess.add_argument("--priority", required=True, choices=priorities.VALUES)
    assess.add_argument("--reason", required=True)
    assess.add_argument("--proposal", required=True)
    assess.add_argument('--recommendation', choices=('fix', 'leave', 'discuss'))
    decide = commands.add_parser("decide")
    decide.add_argument("ids", nargs='+')
    decide.add_argument("--action", choices=("fix", "reject", "defer", "pending"), required=True)
    decide.add_argument("--reason", required=True)
    batch = commands.add_parser("batch-open")
    batch.add_argument("ids", nargs="+")
    done = commands.add_parser("batch-done")
    done.add_argument("--summary", required=True)
    done.add_argument("--validation", required=True)
    done.add_argument('--finding', action='append')
    cancel_batch = commands.add_parser("batch-cancel")
    cancel_batch.add_argument("--reason", required=True)
    for name in ("stop", "reopen"):
        change = commands.add_parser(name)
        change.add_argument("--reason", required=True)
    note = commands.add_parser("note")
    note.add_argument("--text-file", type=Path, required=True)
    imported = commands.add_parser("import-finding")
    imported.add_argument("round", metavar='REVIEWER')
    imported.add_argument("--markdown-file", type=Path, required=True)
    imported.add_argument("--source-artifact", required=True)
    imported.add_argument("--reason", required=True)
    export = commands.add_parser("export")
    export.add_argument("--finding", action="append")
    export.add_argument("--format", choices=("json", "markdown"), default="json")
    return result


def launch_round(journal, number, idle_timeout=300, socket=None, caller_pane=None):
    if idle_timeout <= 0:
        raise ReviewError("Idle timeout must be positive")
    record = journal.round(number)
    if record["status"] != "prepared":
        raise ReviewError("Only a prepared round can be started; never restart a past round")
    if "runtime" not in record:
        raise ReviewError("This legacy round uses a user launcher; cancel it and prepare a managed round")
    if not same_source(record["source"], snapshot(journal.manifest["repo"], journal.manifest["base"])):
        raise ReviewError("Source changed since preparation; cancel this round and prepare another")
    journal.update_round(number, expected={"prepared"}, status="starting", idle_timeout=idle_timeout)
    try:
        target = tmux.launch(journal, number, socket, caller_pane)
    except Exception as error:
        status = "interrupted" if journal.round(number).get("cancel_requested") else "failed"
        journal.update_round(number, status=status, error="Launch failed: " + str(error), finished_at=now())
        raise
    return {"round": round_name(record), "tmux": target, "artifacts": str(journal.round_directory(number))}


def cancel(journal, number):
    record = journal.round(number)
    if record["status"] in {"prepared", "preparing"}:
        return journal.update_round(number, expected={record["status"]}, status="interrupted", cancel_requested=True, finished_at=now(), error="Cancelled before launch")
    return journal.update_round(number, expected={"starting", "running", "cancelling"}, status="cancelling", cancel_requested=True)


def recover(journal, number):
    record = journal.round(number)
    if record["status"] not in ACTIVE:
        raise ReviewError("Round is already terminal")
    if tmux.alive(record):
        raise ReviewError("Reviewer pane is still alive; inspect it or request cancellation")
    if record.get('worker_pid'):
        try:
            os.kill(record['worker_pid'], 0)
        except ProcessLookupError:
            pass
        else:
            raise ReviewError('Reviewer worker may still be running; inspect it before recovery')
    if record.get("reviewer_pid") and record.get('runtime', {}).get('mode') != 'docker':
        try:
            os.kill(record["reviewer_pid"], 0)
        except ProcessLookupError:
            pass
        else:
            raise ReviewError("The recorded reviewer PID still exists; inspect it before recovery")
    journal.update_round(number, expected=ACTIVE, status="interrupted", error="Reviewer pane disappeared before a terminal result was saved", finished_at=now())
    return tmux.close_session(journal, number)


def markdown(data, selected):
    findings = data["findings"]
    if selected:
        missing = set(selected) - {item["id"] for item in findings}
        if missing:
            raise ReviewError("Unknown findings: " + ", ".join(sorted(missing)))
        findings = [item for item in findings if item["id"] in selected]
    lines = ["Local review decisions", "", "Session: " + data["session"]["id"], ""]
    for item in findings:
        lines += ["### " + item["id"] + " — " + item["title"], "", item["body"], "", "Decision: " + item["decision"], "Reason: " + item.get("reason", "Not discussed"), ""]
        if item.get("path"):
            lines += ["Location: " + item["path"] + (":" + str(item["line"]) if item.get("line") else ""), ""]
        if item.get("fix"):
            lines += ["Fix: " + item["fix"]["summary"], "Validation: " + item["fix"]["validation"], ""]
        if item.get("verification"):
            lines += ["Reviewer recheck: " + item["verification"]["status"] + " — " + item["verification"]["reason"], ""]
    return "\n".join(lines)


def execute(args):
    repo = root(args.repo)
    if args.command == 'instructions':
        return configuration.skill_prompt(args.phase)
    if args.command == 'preflight':
        settings = runtime.configuration(repo)
        selected = configuration.load_review(repo)
        tools = ('git', 'tmux', 'docker') if settings['runtime'] == 'docker' else ('git', 'tmux')
        paths = {name: shutil.which(name) for name in tools}
        missing = [name for name, path in paths.items() if path is None]
        if missing:
            raise ReviewError('Missing required tools: ' + ', '.join(missing))
        auth = runtime.check_credentials(settings)
        base = args.base
        session = repo / '.agr/session.json'
        if base is None and session.is_file():
            base = read_json(session)['base']
        if base is None:
            raise ReviewError('Select the review base with --base; the helper does not guess it')
        head = resolve(repo, 'HEAD')
        base_commit = resolve(repo, base)
        from .source import git
        merge_base = git(repo, 'merge-base', base_commit, head).decode().strip()
        result = {
            'tools': paths, 'base': base, 'base_commit': base_commit, 'head': head, 'merge_base': merge_base,
            **settings, **{key: selected[key] for key in configuration.REVIEW_KEYS},
            'defaults': str(configuration.SKILL / ('defaults-codex.ini' if selected['agent'] == 'codex' else 'defaults.ini')),
            'global_config': str(configuration.global_path()), 'worktree_config': str(repo / '.agr/config.ini'),
            'authentication': auth,
        }
        if settings['runtime'] == 'docker':
            result.update(runtime.docker_identity())
        else:
            result['sandbox'] = runtime.native_sandbox(selected['agent'])['message']
        return '\n'.join(key + ': ' + str(value) for key, value in result.items()) if args.human else result
    if args.command == "setup":
        return runtime.setup(repo, runtime.configuration(repo))
    if args.command == "init":
        resolve(repo, args.base)
        journal = Journal.create(repo, args.base, args.task_file.read_text(encoding="utf-8"))
        return {"session": journal.manifest["id"], "directory": str(journal.directory)}
    if args.command == "sessions":
        path = repo / ".agr" / "session.json"
        return [read_json(path)] if path.is_file() else []
    journal = Journal.resolve(repo, args.session)
    if journal.manifest["repo"] != str(repo):
        raise ReviewError("Journal belongs to another worktree; use its original --repo")
    command = args.command
    if hasattr(args, 'round'):
        args.round = journal.select_round(args.round)
    if command == "prepare":
        parallel_with = journal.select_round(args.parallel_with) if args.parallel_with else None
        values = configuration.local(repo)
        selected = configuration.load_review(repo, {key: getattr(args, key) for key in configuration.REVIEW_KEYS}, values=values)
        if selected['scope'] == 'changes':
            prior = journal.round(parallel_with).get('previous_review') if parallel_with is not None else previous_review(journal.export())
            if prior is None:
                raise ReviewError('Changes-only review needs a previous completed review; select scope full')
        settings = runtime.setup(repo, runtime.configuration(repo, values, agent=selected['agent']))
        record = prepare(journal, settings, parallel_with=parallel_with, selection=selected)
        return progress.describe(journal, record['id']) if args.human else record
    if command == "start":
        result = launch_round(journal, args.round, args.idle_timeout)
        return progress.describe(journal, args.round) if args.human else result
    if command == 'watch':
        return progress.watch(journal, args.round)
    if command == 'send':
        record = journal.round(args.round)
        if record['status'] not in {'running', 'completed'} or not record.get('prompt_sent_at') or not record.get('session_open') or record.get('close_requested'):
            raise ReviewError('Input requires an open interactive session with its initial prompt submitted')
        if args.text_file:
            tmux.send_file(record, args.text_file.resolve())
        else:
            tmux.send_key(record, args.key)
        return {'round': round_name(record), 'sent': True}
    if command == 'close':
        return tmux.close_session(journal, args.round)
    if command == "cancel":
        return cancel(journal, args.round)
    if command == "recover":
        return recover(journal, args.round)
    if command == "status":
        if args.human:
            return progress.status(journal)
        data = journal.export()
        counts = {}
        for finding in data["findings"]:
            counts[finding["decision"]] = counts.get(finding["decision"], 0) + 1
        return {"session": journal.manifest, "status": data["status"], "rounds": data["rounds"], "finding_counts": counts, "active_batch": data["active_batch"], "directory": str(journal.directory)}
    if command in {"next", "queue", "finding"}:
        findings = journal.findings()
        if command == 'queue' and args.priority:
            selected = {priorities.canonical(value) for value in args.priority}
            findings = [item for item in findings if item['priority'] in selected]
        if command == 'queue' and args.table:
            return progress.finding_table(findings, journal.rows('rounds'))
        if command == "next":
            findings = [item for item in findings if item['decision'] == 'pending'][:1]
            return progress.findings(findings) if args.human else next(iter(findings), None)
        if command == "finding":
            result = next((item for item in findings if item["id"] == args.id), None)
            if result is None:
                raise ReviewError("Unknown finding: " + args.id)
            return progress.findings([result], detail=True) if args.human else result
        return progress.findings(findings) if args.human else findings
    if command == "assess":
        return journal.author_event("assessment", finding=args.id, priority=args.priority, reason=args.reason, proposal=args.proposal, recommendation=args.recommendation)
    if command == "decide":
        events = journal.decide(args.ids, args.action, args.reason)
        return events[0] if len(events) == 1 else events
    if command == "batch-open":
        return journal.open_batch(args.ids)
    if command in {"batch-done", "batch-cancel"}:
        cancelled = command == "batch-cancel"
        source = snapshot(repo, journal.manifest["base"])
        return journal.close_batch(args.reason if cancelled else args.summary, "" if cancelled else args.validation, source, cancel=cancelled, identifiers=None if cancelled else args.finding)
    if command in {"stop", "reopen"}:
        return journal.set_state("stopped" if command == "stop" else "active", args.reason)
    if command == "note":
        body = args.text_file.read_text(encoding="utf-8")
        if not body.strip():
            raise ReviewError("A context note cannot be empty")
        with journal.access(True) as access:
            return journal.event(access, "context", body=body)
    if command == "import-finding":
        from .documents import draft
        values = draft(args.markdown_file, 'finding')
        directory = journal.round_directory(args.round).resolve()
        artifact = (directory / args.source_artifact).resolve()
        if not artifact.is_relative_to(directory) or not artifact.is_file() or not args.reason.strip():
            raise ReviewError("Supply an existing raw artifact from this round and an import reason")
        return journal.add_finding(args.round, values, imported={"artifact": str(artifact), "reason": args.reason, "at": now()}, draft_name=args.markdown_file.stem)
    if command == "report":
        return {
            "round": journal.round(args.round),
            "findings": [item for item in journal.findings() if item["round"] == args.round],
            "reports": [item for item in journal.rows("events") if item.get("round") == args.round and item["kind"] in {"report", "verification"}],
            "artifacts": str(journal.round_directory(args.round)),
        }
    data = journal.export()
    if args.format == "markdown":
        return markdown(data, args.finding)
    if args.finding:
        markdown(data, args.finding)
        data["findings"] = [item for item in data["findings"] if item["id"] in args.finding]
        data["events"] = [item for item in data["events"] if item.get("finding") in args.finding]
    return data


def main(argv=None):
    os.umask(0o077)
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] == 'publish':
            publish_parser = argparse.ArgumentParser(description='Publish one Markdown draft or finish this review')
            publish_parser.add_argument('output', type=Path)
            publish_parser.add_argument('kind', choices=('finding', 'check', 'report', 'finish'))
            publish_parser.add_argument('--file', type=Path)
            args = publish_parser.parse_args(argv[1:])
            if args.kind != 'finish' and args.file is None:
                raise ReviewError('Supply --file with the Markdown draft')
            from .reporting import publish
            result = publish(args.output, args.kind, args.file)
            print(result.get('id') or result.get('kind') or 'finished')
            return 0
        if argv and argv[0] in {"_worker", "_review"}:
            if len(argv) != 3:
                raise ReviewError("Internal command expects a journal path and round number")
            if argv[0] == "_review":
                runtime.inner_review(Journal(argv[1], readonly=True), int(argv[2]))
                return 0
            from .runner import worker
            return worker(argv[1], int(argv[2]))
        args = parser().parse_args(argv)
        result = execute(args)
        if args.command == 'watch':
            return 0 if result in (None, 'completed') else 1
        print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ReviewError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
