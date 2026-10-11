import json
import os
import signal
import stat
import shutil
import subprocess
import sys
import tempfile
import time

from . import ReviewError, now, read_json, write_json
from .source import same_source, snapshot
from .store import Journal
from . import reporting, runtime, tmux, codex, credentials


def subscription_auth(launcher, repo):
    forbidden = [name for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL") if os.environ.get(name)]
    forbidden += [name for name in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY") if os.environ.get(name, "").lower() not in ("", "0", "false")]
    if forbidden:
        raise ReviewError("Subscription-only review refuses these environment settings: " + ", ".join(forbidden))
    result = subprocess.run(launcher + ["auth", "status"], cwd=repo, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ReviewError("Claude auth status failed; no review was launched")
    try:
        auth = json.loads(result.stdout)
    except ValueError as error:
        raise ReviewError("Claude auth status did not return JSON") from error
    if not isinstance(auth, dict) or auth.get("loggedIn") is not True or auth.get("authMethod") != "claude.ai" or auth.get("apiProvider") != "firstParty" or not auth.get("subscriptionType"):
        raise ReviewError("Claude must report an active first-party claude.ai subscription; no API fallback")
    return {key: auth[key] for key in ("authMethod", "apiProvider", "subscriptionType")}



def claude_command(record, directory):
    args = [record['runtime']['executable'],
        '--session-id', record['session_id'],
        '--permission-mode', 'bypassPermissions', '--setting-sources', '',
        '--settings', json.dumps(runtime.claude_settings(record['runtime']), separators=(',', ':')), '--strict-mcp-config',
        '--disallowedTools', 'AskUserQuestion,EnterPlanMode,ExitPlanMode',
    ]
    if record.get('model'):
        args += ['--model', record['model']]
    if record.get('effort'):
        args += ['--effort', record['effort']]
    return args


def reviewer_command(record, directory):
    return codex.command(record, directory) if record['reviewer'] == 'codex' else claude_command(record, directory)


def end_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def input_ready(screen, agent='claude'):
    if agent == 'codex':
        return codex.input_ready(screen)
    return any(line.strip() == '❯' for line in screen.splitlines())


def file_activity(paths):
    result = []
    for path in paths:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(metadata.st_mode):
            result.append((str(path), metadata.st_size, metadata.st_mtime_ns))
    return result


def observe(process, journal, number, directory, interrupted, bridge=None):
    record = journal.round(number)
    output = directory / 'output'
    last_progress = time.monotonic()
    last_heartbeat = 0
    previous = None
    submitted = False
    startup_deadline = time.monotonic() + 60
    while True:
        if bridge:
            bridge.sync()
        moment = time.monotonic()
        current = journal.round(number)
        if interrupted[0] or current.get('cancel_requested'):
            return {'status': 'interrupted', 'error': 'Review was interrupted'}
        if (output / 'complete.json').exists():
            reporting.complete(output)
            return {'status': 'completed'}
        if process.poll() is not None:
            return {'status': 'failed', 'error': 'Interactive ' + record['reviewer'].capitalize() + ' exited without a validated completion marker', 'exit_code': process.returncode}
        paths = [directory / 'terminal.log', output / 'progress.txt'] + reporting.files(output)
        activity = file_activity(paths)
        if activity != previous:
            previous = activity
            last_progress = moment
        if not submitted:
            if (output / '.runtime.json').is_file():
                screen = tmux.capture(current)
                if input_ready(screen, record['reviewer']):
                    auth = read_json(output / '.runtime.json')
                    tmux.send_file(current, directory / 'prompt.md')
                    journal.update_round(number, prompt_sent_at=now(), auth=auth['auth'], runtime_version=auth['version'])
                    submitted = True
                    last_progress = moment
            if not submitted and moment >= startup_deadline:
                return {'status': 'failed', 'error': record['reviewer'].capitalize() + ' did not reach the interactive input prompt; inspect terminal.log'}
        elif moment - last_progress >= record.get('idle_timeout', 300):
            return {'status': 'stalled', 'error': 'No terminal output or published findings before the idle timeout'}
        if moment - last_heartbeat >= 5:
            journal.update_round(number, heartbeat_at=now())
            last_heartbeat = moment
        time.sleep(0.1)


def keep_session(process, journal, number, interrupted, bridge=None):
    last_heartbeat = 0
    while process.poll() is None:
        if bridge:
            bridge.sync()
        record = journal.round(number)
        if interrupted[0] or record.get('close_requested'):
            return
        moment = time.monotonic()
        if moment - last_heartbeat >= 5:
            journal.update_round(number, heartbeat_at=now())
            last_heartbeat = moment
        time.sleep(0.1)


def worker(directory, number):
    journal = Journal(directory)
    record = journal.round(number)
    deadline = time.monotonic() + 15
    while record['status'] == 'starting' and not record.get('launch_ready') and time.monotonic() < deadline:
        time.sleep(0.1)
        record = journal.round(number)
    if record['status'] != 'starting' or not record.get('launch_ready'):
        if record['status'] in {'starting', 'cancelling'}:
            journal.update_round(number, status='interrupted' if record['status'] == 'cancelling' else 'failed', error='Launch handshake did not complete', finished_at=now())
        return 1
    output = journal.round_directory(number)
    interrupted = [False]
    handlers = {}
    process = None
    home = None
    bridge = None
    finished_at = None
    outcome = {'status': 'failed', 'error': 'Worker did not complete'}
    session_error = None
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        handlers[signum] = signal.signal(signum, lambda *_: interrupted.__setitem__(0, True))
    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ReviewError('Interactive review requires a real tmux terminal on stdin and stdout')
        journal.update_round(number, expected={'starting'}, status='running', worker_pid=os.getpid(), started_at=now(), heartbeat_at=now())
        if not same_source(record['source'], snapshot(journal.manifest['repo'], journal.manifest['base'])):
            raise ReviewError('Source changed since preparation; prepare a new round')
        managed = record['runtime']
        if managed.get('auth') == 'keychain' and (record['reviewer'] == 'codex' or managed['mode'] == 'docker'):
            bridge = credentials.Bridge(managed)
            credential_file = bridge.open()
            record = journal.update_round(number, runtime={**managed, 'credentials_file': credential_file, 'credential_transport': 'file'})
        if record['runtime']['mode'] == 'docker':
            args = runtime.container_command(journal, number)
            environment = runtime.docker_client(record['runtime'])['environment']
            record = journal.update_round(number, container='agr-' + record['session_id'])
        else:
            cache = journal.directory / '.cache'
            cache.mkdir(exist_ok=True)
            home = tempfile.mkdtemp(prefix='runtime-', dir=cache)
            environment = runtime.environment(home, journal.manifest['repo'], agent=record['reviewer'])
            sandbox = record['runtime'].get('sandbox') or {'message': 'Native mode has no recorded Bash sandbox; commands may run with your user permissions.'}
            print(sandbox['message'], flush=True)
            args = [sys.executable, str(runtime.ENTRY), '_review', str(journal.directory), str(number)]
        write_json(output / 'launch.json', {'process': args, record['reviewer']: reviewer_command(record, output)})
        process = subprocess.Popen(args, cwd=journal.manifest['repo'], env=environment)
        journal.update_round(number, reviewer_pid=process.pid, session_open=True)
        outcome = observe(process, journal, number, output, interrupted, bridge)
        if outcome['status'] == 'completed' and not same_source(record['source'], snapshot(journal.manifest['repo'], journal.manifest['base'])):
            outcome.update(status='source_changed', error='Source changed during review; retained findings refer to the prepared snapshot')
        if outcome['status'] == 'completed':
            finished = journal.update_round(number, expected={'running'}, **outcome, finished_at=now())
            finished_at = finished['finished_at']
            keep_session(process, journal, number, interrupted, bridge)
    except Exception as error:
        if finished_at:
            session_error = str(error)
        else:
            cancelled = interrupted[0] or journal.round(number).get('cancel_requested')
            outcome = {'status': 'interrupted' if cancelled else 'failed', 'error': str(error)}
    finally:
        cleanup_errors = []
        actions = [lambda: end_process(process)]
        if record.get('container'):
            actions.append(lambda: runtime.cleanup_container(record))
        if bridge:
            actions.append(bridge.close)
        if home:
            actions.append(lambda: shutil.rmtree(home))
        for action in actions:
            try:
                action()
            except Exception as error:
                cleanup_errors.append(str(error))
        if cleanup_errors:
            session_error = '; '.join(filter(None, [session_error, 'Runtime cleanup failed: ' + '; '.join(cleanup_errors)]))
            if not finished_at:
                outcome.update(status='failed', error='; '.join(filter(None, [outcome.get('error'), session_error])))
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        journal.update_round(number, **outcome, finished_at=finished_at or now(), session_open=False, closed_at=now(), session_error=session_error, cleanup_complete=not cleanup_errors)
        print(outcome['status'] + (': ' + outcome['error'] if outcome.get('error') else ''), flush=True)
    return 0 if outcome['status'] == 'completed' else 1
