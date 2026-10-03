import os
from pathlib import Path
import shlex
import re
import time
import subprocess
import sys

from . import ReviewError, now, runtime
from .documents import atomic_text
from .prompts import ENTRY
from .source import git
from .store import ACTIVE
from .names import round_name


def command(socket=None):
    return ["tmux"] + (["-S", socket] if socket else [])


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ReviewError("tmux: " + result.stderr.strip())
    return result.stdout.strip()


def window_name(journal, number):
    record = journal.round(number)
    name = record.get("runtime", {}).get("window_name")
    if not name:
        repo = Path(journal.manifest["repo"])
        common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip())
        name = common.parent.name[:24] + "/" + record["source"]["branch"][:24]
    name = re.sub(r"[^A-Za-z0-9_./-]+", "-", name).strip("-") or "review"
    return 'agr@' + name + '-' + round_name(record)


def cleanup_windows(journal, current):
    current_pass = journal.round(current).get('pass', current)
    for record in journal.rows("rounds"):
        if record.get('pass', record['id']) >= current_pass or record["status"] in ACTIVE:
            continue
        if record.get('session_error'):
            raise ReviewError('Previous session ' + round_name(record) + ' has a cleanup error: ' + record['session_error'] + '; inspect it and explicitly close that session before starting another')
        state = pane_state(record)
        if state in {'alive', 'dead'} or record.get('session_open') or (record.get('container') and not cleanup_finished(record)):
            close_session(journal, record['id'])


def cleanup_finished(record):
    return record.get('cleanup_complete', bool(record.get('closed_at') and not record.get('session_open') and not record.get('session_error')))


def cleanup_orphan(journal, record):
    if cleanup_finished(record) and not record.get('session_error'):
        return record
    if record.get('worker_pid'):
        try:
            os.kill(record['worker_pid'], 0)
        except ProcessLookupError:
            pass
        else:
            raise ReviewError('Reviewer worker may still be running; inspect it before cleaning the retained runtime')
    if record.get('runtime', {}).get('mode') != 'docker':
        if record.get('session_open') or record.get('session_error'):
            raise ReviewError('Reviewer pane disappeared before cleanup; inspect the retained native runtime')
        return record
    try:
        if record.get('container') or record.get('session_open') or record.get('session_error'):
            runtime.cleanup_container(record)
    except Exception as error:
        journal.update_round(record['id'], session_error='Runtime cleanup failed: ' + str(error), cleanup_complete=False)
        raise
    values = {'session_open': False, 'cleanup_complete': True, 'closed_at': record.get('closed_at') or now(), 'session_error': None}
    if record.get('session_error'):
        values['previous_session_error'] = record['session_error']
    return journal.update_round(record['id'], **values)


def pane_state(record):
    target = record.get('tmux')
    if not target:
        return None
    result = subprocess.run(command(target['socket']) + [
        'display-message', '-p', '-t', target['pane'],
        '#{pane_id}\t#{window_id}\t#{pane_dead}\t#{@agr_token}',
    ], capture_output=True, text=True, timeout=15)
    if result.returncode:
        if any(message in result.stderr for message in ("can't find pane", 'no server running', 'No such file or directory', "can't find window", "can't find session")):
            return None
        raise ReviewError('Cannot inspect reviewer pane: ' + result.stderr.strip())
    fields = result.stdout.rstrip('\r\n').split('\t')
    if len(fields) != 4:
        raise ReviewError('Unexpected tmux pane response')
    if fields[0] != target['pane']:
        return None
    if fields[1] != target['window'] or fields[3] != record['session_id']:
        return 'foreign'
    return 'dead' if fields[2] == '1' else 'alive'


def close_session(journal, number):
    record = journal.round(number)
    if record['status'] in ACTIVE:
        raise ReviewError('Cancel the active review before closing its session')
    state = pane_state(record)
    if state == 'foreign':
        raise ReviewError('Reviewer pane ownership changed; left untouched')
    if state is None:
        return cleanup_orphan(journal, record)
    target = record['tmux']
    base = command(target['socket'])
    screen = journal.round_directory(number) / 'screen.txt'
    if state == 'alive' or not screen.exists():
        text = run(base + ['capture-pane', '-p', '-J', '-t', target['pane'], '-S', '-'])
        atomic_text(screen, text + '\n', replace=True)
    if state == 'alive':
        if not record.get('session_open'):
            raise ReviewError('The terminal still has a process without an open managed session')
        journal.update_round(number, close_requested=True)
        deadline = time.monotonic() + 15
        while (record.get('session_open') or state == 'alive') and time.monotonic() < deadline:
            time.sleep(0.1)
            record = journal.round(number)
            state = pane_state(record)
        if record.get('session_open') or state == 'alive':
            raise ReviewError('Session closure is still pending; inspect its recorded state')
        if record.get('session_error'):
            raise ReviewError('Session cleanup failed: ' + record['session_error'])
    record = cleanup_orphan(journal, record)
    if record.get('session_error'):
        raise ReviewError('Session cleanup failed: ' + record['session_error'])
    state = pane_state(record)
    if state == 'dead':
        run(base + ['kill-pane', '-t', target['pane']])
    elif state is not None:
        raise ReviewError('Pane state changed during closure; left untouched')
    return record


def launch(journal, number, socket=None, caller_pane=None):
    tmux_env = os.environ.get("TMUX", "")
    socket = socket or (tmux_env.rsplit(",", 2)[0] if tmux_env else None)
    caller_pane = caller_pane if caller_pane is not None else os.environ.get("TMUX_PANE")
    base = command(socket)
    name = window_name(journal, number)
    cleanup_windows(journal, number)
    fields = "#{socket_path}\t#{session_id}\t#{window_id}\t#{pane_id}"
    worker = shlex.join([sys.executable, str(ENTRY), "_worker", str(journal.directory), str(number)])
    if caller_pane:
        session = run(base + ["display-message", "-p", "-t", caller_pane, "#{session_id}"])
        args = ["new-window", "-d", "-t", session, "-n", name]
    else:
        args = ["new-session", "-d", "-s", "agr-" + journal.round(number)["session_id"][:8], "-n", name, "-x", "140", "-y", "40"]
    output = run(base + args + ["-P", "-F", fields, "-c", journal.manifest["repo"], worker])
    parts = output.split("\t")
    if len(parts) != 4 or not all(parts):
        raise ReviewError("Unexpected tmux launch response: " + output)
    target = dict(zip(("socket", "session", "window", "pane"), parts))
    journal.update_round(number, tmux=target)
    run(command(target["socket"]) + ["set-option", "-w", "-t", target["window"], "remain-on-exit", "on"])
    run(command(target["socket"]) + ["set-option", "-w", "-t", target["window"], "automatic-rename", "off"])
    run(command(target["socket"]) + ["set-option", "-p", "-t", target["pane"], "@agr_token", journal.round(number)["session_id"]])
    run(command(target["socket"]) + ["set-option", "-w", "-t", target["window"], "allow-rename", "off"])
    log = journal.round_directory(number) / 'terminal.log'
    run(command(target['socket']) + ['pipe-pane', '-o', '-t', target['pane'], 'cat >> ' + shlex.quote(str(log))])
    target['attach'] = shlex.join(command(target['socket']) + ['attach-session', '-t', target['session']])
    journal.update_round(number, tmux=target)
    journal.update_round(number, expected={"starting"}, launch_ready=True)
    return target


def alive(record):
    target = record.get("tmux")
    if not target:
        raise ReviewError("No tmux target was recorded; inspect the retained launch state before recovery")
    result = subprocess.run(command(target["socket"]) + [
        "display-message", "-p", "-t", target["pane"], "#{pane_id}\t#{pane_dead}\t#{@agr_token}",
    ], capture_output=True, text=True, timeout=15)
    if result.returncode:
        error = result.stderr.strip()
        if any(message in error for message in ("can't find pane", "no server running", "No such file or directory", "can't find window", "can't find session")):
            return False
        raise ReviewError("Cannot determine whether the review is alive: " + error)
    if not result.stdout.strip():
        return False
    fields = result.stdout.rstrip("\r\n").split("\t")
    if len(fields) != 3:
        raise ReviewError("Unexpected tmux status response")
    return fields[0] == target["pane"] and fields[1] == "0" and fields[2] == record["session_id"]


def capture(record):
    if not alive(record):
        raise ReviewError('Reviewer pane is no longer owned and alive')
    target = record['tmux']
    return run(command(target['socket']) + ['capture-pane', '-p', '-t', target['pane'], '-S', '-80'])


def send_file(record, path):
    if not alive(record):
        raise ReviewError('Reviewer pane is no longer owned and alive')
    target = record['tmux']
    base = command(target['socket'])
    buffer = 'agr-' + record['session_id']
    run(base + ['load-buffer', '-b', buffer, str(path)])
    run(base + ['paste-buffer', '-b', buffer, '-d', '-p', '-t', target['pane']])
    time.sleep(0.2)
    run(base + ['send-keys', '-t', target['pane'], 'Enter'])


def send_key(record, key):
    if key not in {'Enter', 'Escape', 'C-c', 'C-d', 'Up', 'Down'}:
        raise ReviewError('Unsupported terminal control key')
    if not alive(record):
        raise ReviewError('Reviewer pane is no longer owned and alive')
    target = record['tmux']
    run(command(target['socket']) + ['send-keys', '-t', target['pane'], key])
