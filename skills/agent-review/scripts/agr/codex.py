import json
import os
from pathlib import Path
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


def install_binary(destination):
    from .runtime import digest, platform_name
    destination = Path(destination)
    name = platform_name().removesuffix('-musl')
    expected = RELEASE['platforms'][name]
    archive = destination.with_name('codex-release.tar.gz')
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
            url = DOWNLOADS + RELEASE['version'] + '/' + expected['binary'] + '.tar.gz'
            print('Downloading Codex ' + RELEASE['version'] + ' for ' + name, file=sys.stderr, flush=True)
            subprocess.run(['curl', '--fail', '--location', '--show-error', '--connect-timeout', '30', '--max-time', '600', '--output', str(temporary), url], check=True, stdout=sys.stderr)
            if temporary.stat().st_size != expected['size'] or digest(temporary) != expected['checksum']:
                raise ReviewError('Downloaded Codex did not match its pinned size and SHA-256')
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)
    if archive.stat().st_size != expected['size'] or digest(archive) != expected['checksum']:
        raise ReviewError('Managed Codex archive checksum mismatch: ' + str(archive))
    descriptor, filename = tempfile.mkstemp(prefix='binary-', dir=destination.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, 'wb') as output, tarfile.open(archive, 'r:gz') as bundle:
            members = [member for member in bundle.getmembers() if member.name == expected['binary']]
            if len(members) != 1 or not members[0].isfile():
                raise ReviewError('Codex archive must contain one regular binary: ' + expected['binary'])
            with bundle.extractfile(members[0]) as source:
                shutil.copyfileobj(source, output)
        if destination.exists():
            if not destination.is_file() or digest(destination) != digest(temporary):
                raise ReviewError('Managed Codex checksum mismatch: ' + str(destination))
        else:
            temporary.chmod(0o755)
            os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


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
