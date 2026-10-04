import csv
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

from . import ReviewError, read_json, write_json
from . import configuration as review_config
from . import codex
from . import agents, credentials
from .source import git
from .store import local_directory


SKILL = Path(__file__).resolve().parents[2]
ENTRY = SKILL / "scripts" / "review.py"
RELEASE = read_json(SKILL / "runtime-release.json")
DOWNLOADS = "https://downloads.claude.ai/claude-code-releases"
CONTAINER_CREDENTIALS = "/run/agr/credentials.json"


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def platform_name():
    system = {"Linux": "linux", "Darwin": "darwin"}.get(platform.system())
    architecture = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine())
    if not system or not architecture:
        raise ReviewError("Managed reviewers require Linux or macOS on x64 or ARM64")
    name = system + "-" + architecture
    if system == "linux" and (list(Path("/lib").glob("libc.musl-*.so.1")) or platform.libc_ver()[0] == "musl"):
        name += "-musl"
    return name


def install_binary(destination):
    destination = Path(destination)
    name = platform_name()
    expected = RELEASE["platforms"][name]
    if destination.is_file():
        if digest(destination) != expected["checksum"]:
            raise ReviewError("Managed Claude checksum mismatch: " + str(destination))
        return destination
    if not shutil.which("curl"):
        raise ReviewError("curl is required to download managed Claude")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix="download-", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(filename)
    try:
        url = DOWNLOADS + "/" + RELEASE["version"] + "/" + name + "/claude"
        print("Downloading Claude " + RELEASE["version"] + " for " + name, file=sys.stderr, flush=True)
        subprocess.run(["curl", "--fail", "--location", "--show-error", "--connect-timeout", "30", "--max-time", "600", "--output", str(temporary), url], check=True, stdout=sys.stderr)
        if temporary.stat().st_size != expected["size"] or digest(temporary) != expected["checksum"]:
            raise ReviewError("Downloaded Claude did not match its pinned size and SHA-256")
        temporary.chmod(0o755)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def configuration(repo, values=None, global_values=None, agent=None, overrides=None):
    selected = {**(overrides or {})}
    if agent is not None:
        selected['agent'] = agent
    values = review_config.effective(repo, values, global_values, selected)
    agent = values['agent'].strip()
    mode = values['runtime']
    if mode == 'auto':
        mode = 'docker'
    if mode not in {"docker", "native"}:
        raise ReviewError("Runtime must be auto, docker or native")
    authentication = agents.adapter(agent).authentication(repo, values)
    name = values['window_name'] or None
    if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(character) < 33 or ord(character) > 126 for character in name)):
        raise ReviewError("Window name must contain 1 to 80 ASCII characters without whitespace")
    return {"runtime": mode, 'agent': agent, "window_name": name, **authentication}


def auth_source(settings):
    selected = settings.get('auth', 'auto')
    if selected == 'auto':
        selected = 'keychain' if settings.get('agent', 'claude') == 'claude' and platform.system() == 'Darwin' and settings['runtime'] == 'native' else 'file'
    return selected


def keychain_account():
    return agents.adapter('claude').account()


def check_credentials(settings):
    selected = {**settings, 'auth': auth_source(settings)}
    if selected['auth'] == 'keychain':
        selected.setdefault('keychain_service', 'Claude Code-credentials')
        selected.setdefault('keychain_account', keychain_account())
    return credentials.check(selected)


def prerequisites(settings):
    required = ('git', 'tmux', 'docker') if settings['runtime'] == 'docker' else ('git', 'tmux', 'curl')
    paths = {name: shutil.which(name) for name in required}
    missing = [name for name, path in paths.items() if path is None]
    if missing:
        raise ReviewError('Missing required tools: ' + ', '.join(missing))
    return paths


def native_path():
    return '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin' if platform.system() == 'Darwin' else os.defpath


def native_sandbox(agent='claude'):
    if agent == 'codex':
        return {'enabled': True, 'message': 'Native Codex requests its built-in workspace-write sandbox for the isolated working directory and reviewer output; source is outside those writable roots. Sandbox failures are not retried unsandboxed.'}
    required = ('sandbox-exec',) if platform.system() == 'Darwin' else ('bwrap', 'socat')
    missing = [name for name in required if not shutil.which(name, path=native_path())]
    if missing:
        return {'enabled': False, 'message': 'WARNING: native mode has no Bash sandbox; missing ' + ', '.join(missing) + '. Commands run with your user permissions.'}
    return {'enabled': True, 'message': 'Native mode requests Claude built-in Bash sandbox; Claude warns and continues unsandboxed if it cannot start. This is not whole-process isolation.'}


def claude_settings(managed):
    settings = {'disableAllHooks': True}
    if managed['mode'] == 'native':
        settings['permissions'] = {'blockReadsOutsideWorkingDirectories': True,
                                   'additionalDirectories': managed.get('read_directories', [])}
        settings['sandbox'] = {'enabled': managed.get('sandbox', {}).get('enabled', False),
                               'failIfUnavailable': False, 'autoAllowBashIfSandboxed': True,
                               'allowUnsandboxedCommands': False, 'network': {'allowedDomains': ['*']}}
    return settings


def checked(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60, **kwargs)
    if result.returncode:
        raise ReviewError(result.stderr.strip() or "Command failed: " + args[0])
    return result.stdout.strip()


def image_tag(agent='claude'):
    release = codex.RELEASE if agent == 'codex' else RELEASE
    paths = [SKILL / "Dockerfile", SKILL / ".dockerignore", SKILL / "runtime-release.json", SKILL / 'codex-release.json']
    paths += sorted((SKILL / "scripts").rglob("*.py"))
    value = hashlib.sha256()
    for path in paths:
        value.update(path.relative_to(SKILL).as_posix().encode())
        value.update(path.read_bytes())
    return 'agr-' + agent + ':' + release["version"] + "-" + value.hexdigest()[:16]


def docker_connection():
    executable = shutil.which('docker')
    if not executable:
        if shutil.which('podman'):
            raise ReviewError('Podman is not supported yet; use Docker Engine without userns-remap')
        raise ReviewError('Docker is required for the selected runtime; set [review] runtime = native to use native mode with weaker isolation')
    executable = str(Path(executable).absolute())
    keys = ('HOME', 'PATH', 'USER', 'LOGNAME', 'XDG_RUNTIME_DIR', 'SSH_AUTH_SOCK', 'SSH_AGENT_PID',
            'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
            'DOCKER_API_VERSION', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
            'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')
    environment = {key: os.environ[key] for key in keys if key in os.environ}
    environment.update(HOME=str(Path.home()), LANG='C', LC_ALL='C')
    config = Path(os.environ.get('DOCKER_CONFIG') or Path.home() / '.docker').expanduser().resolve()
    environment['DOCKER_CONFIG'] = str(config)
    context = environment.get('DOCKER_CONTEXT') or ('default' if environment.get('DOCKER_HOST') else checked([executable, 'context', 'show'], env=environment))
    try:
        metadata = json.loads(checked([executable, 'context', 'inspect', context, '--format', '{{json .}}'], env=environment))
        endpoint = metadata['Endpoints']['docker']
        host = endpoint['Host']
        if not isinstance(host, str) or not host:
            raise ValueError('Missing Docker endpoint')
        args = [executable, '--config', str(config), '--host', host]
        if context == 'default':
            verify = bool(environment.get('DOCKER_TLS_VERIFY'))
            tls = verify or bool(environment.get('DOCKER_TLS'))
            certificates = Path(environment.get('DOCKER_CERT_PATH') or config).expanduser().resolve()
            material = [name for name in ('ca.pem', 'cert.pem', 'key.pem') if (certificates / name).is_file()] if tls else []
        else:
            material = metadata.get('TLSMaterial', {}).get('docker', [])
            verify = not endpoint.get('SkipTLSVerify', False)
            tls = bool(material) or not verify
            certificates = Path(metadata['Storage']['TLSPath']) / 'docker' if material else config
        if tls:
            args.append('--tlsverify' if verify else '--tls')
            for name, flag in (('ca.pem', '--tlscacert'), ('cert.pem', '--tlscert'), ('key.pem', '--tlskey')):
                args += [flag, str(certificates / name) if name in material else '']
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ReviewError('Cannot freeze Docker connection from context metadata') from error
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        environment.pop(key, None)
    return {'command': args, 'environment': environment, 'endpoint': host, 'context': context}


def docker_client(managed):
    client = managed.get('docker_client')
    if not client:
        raise ReviewError('Docker connection was not recorded for this round; inspect the retained runtime before cleanup, or prepare a new round before launch')
    return client


def docker_identity(client=None):
    client = client if client is not None else docker_connection()
    try:
        version = json.loads(checked(client['command'] + ['version', '--format', '{{json .}}'], env=client['environment']))
        info = json.loads(checked(client['command'] + ['info', '--format', '{{json .}}'], env=client['environment']))
    except ValueError as error:
        raise ReviewError('Cannot identify the container engine; expected Docker Engine JSON metadata') from error
    if not isinstance(version, dict) or not isinstance(info, dict):
        raise ReviewError('Cannot identify the container engine; expected Docker Engine metadata')
    if 'podman' in json.dumps(version).lower() or {'host', 'store'} <= info.keys():
        raise ReviewError('Podman is not supported yet; use Docker Engine without userns-remap')
    server = version.get('Server')
    components = server.get('Components') if isinstance(server, dict) else None
    if not isinstance(components, list) or not any(isinstance(item, dict) and item.get('Name') == 'Engine' for item in components) or info.get('OSType') != 'linux':
        raise ReviewError('Unknown container engine; only Linux Docker Engine without userns-remap is supported')
    security = info.get('SecurityOptions')
    if not isinstance(security, list) or any(not isinstance(item, str) or not item.startswith('name=') for item in security):
        raise ReviewError('Cannot determine Docker user namespace mode from SecurityOptions')
    modes = {item.split(',', 1)[0] for item in security}
    if 'name=userns' in modes:
        raise ReviewError('Docker userns-remap is not supported yet; no runtime or host permissions were changed')
    rootless = 'name=rootless' in modes
    return {'docker_mode': 'rootless' if rootless else 'rootful', 'user': '0:0' if rootless else str(os.getuid()) + ':' + str(os.getgid())}


def setup(repo, settings):
    prerequisites(settings)
    check_credentials(settings)
    agent = settings.get('agent', 'claude')
    release = codex.RELEASE if agent == 'codex' else RELEASE
    record = {"mode": settings["runtime"], 'agent': agent, "version": release["version"], 'auth': auth_source(settings), "credentials_file": settings['credentials_file'], "window_name": settings["window_name"]}
    for key in ('keychain_service', 'keychain_account'):
        if key in settings:
            record[key] = settings[key]
    if record["mode"] == "native":
        cache = local_directory(repo) / '.cache' / 'runtime'
        if agent == 'codex':
            binary = codex.install_package(cache / 'codex' / release['version'] / platform_name() / 'package')
            record['package_sha256'] = codex.package_digest(binary.parent.parent)
        else:
            binary = install_binary(cache / release['version'] / platform_name() / 'claude')
        record.update(executable=str(binary), sha256=digest(binary), python=sys.executable, entry=str(ENTRY))
        common = git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip()
        record.update(sandbox=native_sandbox(agent), read_directories=[str(SKILL), common])
        print(record['sandbox']['message'], file=sys.stderr, flush=True)
        return record
    client = docker_connection()
    identity = docker_identity(client)
    tag = image_tag(agent)
    images = checked(client['command'] + ["image", "ls", "--no-trunc", "--quiet", tag], env=client['environment'])
    if not images:
        print("Building " + tag, file=sys.stderr, flush=True)
        subprocess.run(client['command'] + ["build", "--progress", "plain", "--build-arg", 'REVIEW_AGENT=' + agent, "--tag", tag, str(SKILL)], env=client['environment'], check=True, stdout=sys.stderr, timeout=1800)
    image = checked(client['command'] + ["image", "inspect", "--format", "{{.Id}}", tag], env=client['environment'])
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ReviewError("Docker returned an invalid image ID")
    record.update(identity, docker_client=client, image=image, image_tag=tag, executable='/opt/agr/codex/bin/codex' if agent == 'codex' else '/opt/agr/bin/claude', python="/usr/local/bin/python3", entry="/opt/agr/scripts/review.py")
    return record


def environment(home, repo, container=False, agent='claude'):
    home = str(home)
    result = {
        "HOME": home, "CLAUDE_CONFIG_DIR": home + "/.claude", "PATH": "/usr/local/bin:/usr/bin:/bin" if container else native_path(),
        "LANG": "en_US.UTF-8" if not container and platform.system() == 'Darwin' else "C.UTF-8",
        "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "IS_SANDBOX": "1",
        "TERM": "xterm-256color",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": str(repo),
    }
    if agent == 'codex':
        for key in ('CLAUDE_CONFIG_DIR', 'DISABLE_AUTOUPDATER', 'DISABLE_UPDATES', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC', 'IS_SANDBOX'):
            del result[key]
        result.update(CODEX_HOME=home + '/.codex', TMPDIR=home + '/tmp')
    return result


def mount(source, destination, readonly=False):
    output = io.StringIO()
    fields = ["type=bind", "source=" + str(source), "target=" + str(destination)]
    if readonly:
        fields.append("readonly")
    csv.writer(output, lineterminator="").writerow(fields)
    return output.getvalue()


def container_command(journal, number):
    record = journal.round(number)
    runtime = record["runtime"]
    repo = Path(journal.manifest["repo"])
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip())
    private = credentials.private_directory().resolve()
    for source in (repo.resolve(), common.resolve()):
        if private.is_relative_to(source) or source.is_relative_to(private):
            raise ReviewError('Source or Git mount would expose private credential storage: ' + str(private))
    name = "agr-" + record["session_id"]
    args = docker_client(runtime)['command'] + ["run", "--rm", "--init", "--interactive", "--tty", "--name", name,
            "--label", "agr.session=" + record["session_id"], "--user", runtime["user"],
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
            "--tmpfs", "/tmp:rw,exec,nosuid,mode=1777", "--workdir", str(repo)]
    args += ["--mount", mount(repo, repo, True)]
    if not common.is_relative_to(repo):
        args += ["--mount", mount(common, common, True)]
    output = journal.round_directory(number) / "output"
    args += ["--mount", mount(output, output)]
    args += ["--mount", mount(runtime["credentials_file"], CONTAINER_CREDENTIALS)]
    for key, value in environment("/tmp/agr-home", repo, container=True, agent=record['reviewer']).items():
        args += ["--env", key + "=" + value]
    args += [runtime["image"], "_review", str(journal.directory), str(number)]
    return args


def cleanup_container(record):
    if record.get("runtime", {}).get("mode") != "docker":
        return
    name = "agr-" + record["session_id"]
    client = docker_client(record['runtime'])
    result = subprocess.run(client['command'] + ["container", "inspect", "--format", '{{json .}}', name], env=client['environment'], capture_output=True, text=True, timeout=15)
    if result.returncode:
        if "No such container" in result.stderr or "No such object" in result.stderr:
            return
        raise ReviewError("Cannot determine container state: " + result.stderr.strip())
    try:
        container = json.loads(result.stdout)
        owner = container['Config']['Labels'].get('agr.session')
        identifier = container['Id']
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{64}', identifier):
            raise ValueError('Invalid container ID')
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ReviewError('Cannot determine container ownership: ' + name) from error
    if owner != record["session_id"]:
        raise ReviewError("Container ownership changed; retained " + name)
    result = subprocess.run(client['command'] + ["container", "rm", "--force", identifier], env=client['environment'], capture_output=True, text=True, timeout=60)
    if result.returncode and 'No such container' not in result.stderr and 'No such object' not in result.stderr:
        raise ReviewError('Container cleanup failed: ' + result.stderr.strip())


def inner_review(journal, number):
    from .runner import claude_command, subscription_auth
    record = journal.round(number)
    runtime = record["runtime"]
    home = Path(os.environ["HOME"])
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if record['reviewer'] == 'codex':
        executable = runtime['executable']
        if runtime['mode'] == 'native':
            if not runtime.get('package_sha256') or codex.package_digest(Path(executable).parent.parent) != runtime['package_sha256']:
                raise ReviewError('Managed Codex package changed or is incomplete; prepare a new round')
        credentials = runtime['credentials_file'] if runtime['mode'] == 'native' else CONTAINER_CREDENTIALS
        codex.initialize(home, credentials)
        os.chdir(home)
        version = checked([executable, '--version'])
        if version != 'codex-cli ' + runtime['version']:
            raise ReviewError('Managed Codex version changed: ' + version)
        auth = codex.subscription_auth([executable], home)
        write_json(journal.round_directory(number) / 'output' / '.runtime.json', {'auth': auth, 'version': runtime['version']})
        args = codex.command(record, journal.round_directory(number))
        os.execve(executable, args, dict(os.environ))
        return
    config = home / ".claude"
    config.mkdir(exist_ok=True, mode=0o700)
    if runtime["mode"] == "native":
        if digest(runtime["executable"]) != runtime["sha256"]:
            raise ReviewError("Managed Claude changed since this round was prepared")
    if runtime.get('auth') == 'keychain' and runtime.get('credential_transport') != 'file':
        os.environ['CLAUDE_SECURESTORAGE_CONFIG_DIR'] = ''
        os.environ['USER'] = runtime['keychain_account']
    else:
        credentials = runtime["credentials_file"] if runtime["mode"] == "native" else CONTAINER_CREDENTIALS
        (config / ".credentials.json").symlink_to(credentials)
    executable = runtime["executable"]
    version = checked([executable, "--version"])
    if version.split()[0] != runtime["version"]:
        raise ReviewError("Managed Claude version changed: " + version)
    auth = subscription_auth([executable, '--setting-sources', '', '--settings', json.dumps(claude_settings(runtime), separators=(',', ':'))], journal.manifest["repo"])
    write_json(home / '.claude.json', {
        'hasCompletedOnboarding': True, 'lastOnboardingVersion': runtime['version'],
        'theme': 'dark', 'bypassPermissionsModeAccepted': True,
        'projects': {journal.manifest['repo']: {'hasTrustDialogAccepted': True, 'hasCompletedProjectOnboarding': True}},
    })
    write_json(config / '.claude.json', read_json(home / '.claude.json'))
    write_json(journal.round_directory(number) / 'output' / '.runtime.json', {'auth': auth, 'version': runtime['version']})
    args = claude_command(record, journal.round_directory(number))
    os.execve(executable, args, dict(os.environ))
