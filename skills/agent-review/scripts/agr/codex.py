import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

from . import ReviewError, read_json


SKILL = Path(__file__).resolve().parents[2]
RELEASE = read_json(SKILL / 'codex-release.json')
DOWNLOADS = 'https://github.com/openai/codex/releases/download/rust-v'


def package_digest(directory):
    from .runtime import digest
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ReviewError('Managed Codex package is not a regular directory: ' + str(directory))
    result = hashlib.sha256()
    for path in sorted(directory.rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ReviewError('Unexpected Codex package entry: ' + str(path))
        if path.is_file():
            result.update(path.relative_to(directory).as_posix().encode() + b'\0')
            result.update(digest(path).encode() + b'\0')
            result.update(str(path.stat().st_mode & 0o111).encode() + b'\0')
    return result.hexdigest()


def validate_package(directory, target):
    metadata = read_json(directory / 'codex-package.json')
    expected = {'layoutVersion': 1, 'version': RELEASE['version'], 'target': target, 'variant': 'codex', 'entrypoint': 'bin/codex', 'resourcesDir': 'codex-resources', 'pathDir': 'codex-path'}
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ReviewError('Codex package metadata does not match its pinned release')
    executables = ['bin/codex', 'bin/codex-code-mode-host', 'codex-path/rg', 'codex-resources/zsh/bin/zsh']
    if 'linux' in target:
        executables.append('codex-resources/bwrap')
    for name in executables:
        path = directory / name
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ReviewError('Codex package is missing an executable: ' + name)


def install_package(destination):
    from .runtime import digest, platform_name
    destination = Path(destination)
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise ReviewError('Managed Codex package is not a regular directory: ' + str(destination))
    name = platform_name().removesuffix('-musl')
    expected = RELEASE['platforms'][name]
    archive = destination.with_name(expected['archive'])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        if destination.exists():
            raise ReviewError('Managed Codex archive is missing: ' + str(archive))
        if not shutil.which('curl'):
            raise ReviewError('curl is required to download managed Codex')
        descriptor, filename = tempfile.mkstemp(prefix='download-', dir=destination.parent)
        os.close(descriptor)
        temporary = Path(filename)
        try:
            url = DOWNLOADS + RELEASE['version'] + '/' + expected['archive']
            print('Downloading Codex package ' + RELEASE['version'] + ' for ' + name, file=sys.stderr, flush=True)
            subprocess.run(['curl', '--fail', '--location', '--show-error', '--connect-timeout', '30', '--max-time', '600', '--output', str(temporary), url], check=True, stdout=sys.stderr)
            if temporary.stat().st_size != expected['size'] or digest(temporary) != expected['checksum']:
                raise ReviewError('Downloaded Codex did not match its pinned size and SHA-256')
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)
    if archive.stat().st_size != expected['size'] or digest(archive) != expected['checksum']:
        raise ReviewError('Managed Codex archive checksum mismatch: ' + str(archive))
    with tempfile.TemporaryDirectory(prefix='package-', dir=destination.parent) as staging:
        temporary = Path(staging) / 'package'
        temporary.mkdir(mode=0o755)
        entries = set()
        with tarfile.open(archive, 'r|gz') as bundle:
            for member in bundle:
                path = PurePosixPath(member.name)
                if not path.parts or path.is_absolute() or '..' in path.parts or path.as_posix() != member.name or member.name in entries or not (member.isfile() or member.isdir()):
                    raise ReviewError('Unexpected Codex archive entry: ' + member.name)
                entries.add(member.name)
                target = temporary.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o755)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                    with bundle.extractfile(member) as source, target.open('xb') as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
        temporary.chmod(0o755)
        for path in temporary.rglob('*'):
            if path.is_dir():
                path.chmod(0o755)
        validate_package(temporary, expected['target'])
        if destination.exists():
            if package_digest(destination) != package_digest(temporary):
                raise ReviewError('Managed Codex package checksum mismatch: ' + str(destination))
        else:
            os.replace(temporary, destination)
    return destination / 'bin/codex'


def subscription_auth(launcher, repo):
    forbidden = [key for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'CODEX_API_ENDPOINT') if os.environ.get(key)]
    if forbidden:
        raise ReviewError('ChatGPT-only review refuses these environment settings: ' + ', '.join(forbidden))
    result = subprocess.run(launcher + ['login', 'status'], cwd=repo, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    if result.returncode or (result.stdout + result.stderr).strip() != 'Logged in using ChatGPT':
        raise ReviewError('Codex must report a ChatGPT login; no API fallback and no review was launched')
    return {'authMethod': 'chatgpt'}


def command(record, directory):
    args = [record['runtime']['executable'], '--no-daemon', '--no-alt-screen']
    if record['runtime']['mode'] == 'docker':
        args += ['--dangerously-bypass-approvals-and-sandbox']
    else:
        args += ['--ask-for-approval', 'never', '--sandbox', 'workspace-write', '--add-dir', str(Path(directory) / 'output')]
    if record.get('model'):
        args += ['--model', record['model']]
    if record.get('effort'):
        args += ['-c', 'model_reasoning_effort=' + json.dumps(record['effort'])]
    return args


def initialize(home, credentials):
    home = Path(home)
    config = home / '.codex'
    config.mkdir(mode=0o700, exist_ok=True)
    (config / 'auth.json').symlink_to(credentials)
    (home / '.agr-review-root').touch()
    (home / 'tmp').mkdir(mode=0o700, exist_ok=True)
    settings = {
        'model_provider': 'openai', 'forced_login_method': 'chatgpt', 'cli_auth_credentials_store': 'file',
        'approval_policy': 'never', 'check_for_update_on_startup': False,
        'project_root_markers': ['.agr-review-root'], 'project_doc_max_bytes': 0,
        'web_search': 'live', 'agents.enabled': True, 'agents.max_concurrent_threads_per_session': 3,
        'sandbox_workspace_write.network_access': True,
        'sandbox_workspace_write.exclude_slash_tmp': True, 'sandbox_workspace_write.exclude_tmpdir_env_var': True,
        'notice.hide_full_access_warning': True, 'notice.hide_rate_limit_model_nudge': True,
        'tui.animations': False, 'tui.auto_recap': False,
        'features.daemon_auto_start': False, 'features.in_app_updates': False,
        'features.external_migration': False, 'features.external_agent_memory_import': False,
        'features.memories': False, 'features.skip_host_skill_discovery': True,
        'features.apps': False, 'features.plugins': False,
        'features.hooks': False, 'features.codex_hooks': False, 'features.plugin_hooks': False,
        'features.default_mode_request_user_input': False,
        'shell_environment_policy.experimental_use_profile': False,
        'projects.' + json.dumps(str(home)) + '.trust_level': 'trusted',
    }
    (config / 'config.toml').write_text(''.join(key + ' = ' + json.dumps(value) + '\n' for key, value in settings.items()), encoding='utf-8')


def input_ready(screen):
    return any(re.fullmatch(r'\s*[›»❯]\s+Ask Codex to do anything\s*', line) for line in screen.splitlines())
