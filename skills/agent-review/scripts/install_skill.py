import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid


RELEASES = 'https://github.com/sintoniastrategy/agent-review/releases'
INSTALL_COMMAND = 'curl -fsSL ' + RELEASES + '/latest/download/install.sh | bash'
INTERVAL = 24 * 60 * 60
REQUIRED = ('SKILL.md', 'defaults.ini', 'runtime-release.json', 'codex-release.json', 'scripts/review.py', 'scripts/install_skill.py', 'scripts/install_codex.py', 'scripts/agr/codex.py')


def download(url, destination):
    result = subprocess.run([
        'curl', '--fail', '--silent', '--show-error', '--location', '--proto', '=https',
        '--tlsv1.2', '--connect-timeout', '10', '--max-time', '60',
        '--output', str(destination), url,
    ], capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Download failed: ' + result.stderr.strip())


def extract(archive, destination):
    with tarfile.open(archive, 'r:gz') as bundle:
        seen = set()
        for member in bundle:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != 'agent-review':
                raise ValueError('Unexpected archive path: ' + member.name)
            if member.name in seen or not (member.isfile() or member.isdir()):
                raise ValueError('Unexpected archive entry: ' + member.name)
            seen.add(member.name)
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, target.open('xb') as output:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
    if not all((destination / 'agent-review' / path).is_file() for path in REQUIRED):
        raise ValueError('Release is missing required skill files')


def managed_root(script):
    script = Path(script).resolve()
    release = script.parents[2]
    if release.parent.name == 'releases' and (release / 'release.json').is_file():
        return release.parent.parent
    return None


def selected_skill(root):
    current = root / 'current'
    if current.is_symlink():
        release = current.resolve(strict=True)
        if release.parent == root / 'releases' and (release / 'agent-review/SKILL.md').is_file():
            return release / 'agent-review'
    if current.exists() or current.is_symlink():
        raise ValueError('Refusing to replace an unmanaged installation: ' + str(current))
    return None


def skill_links(home):
    return [home / '.agents/skills/sst-agent-review', home / '.claude/skills/sst-agent-review']


def check_links(home, target):
    for link in skill_links(home):
        if link.is_symlink() and link.resolve() == target.resolve():
            continue
        if link.exists() or link.is_symlink():
            raise ValueError('Existing skill is not managed by this installer: ' + str(link))


def activate(root, home, release):
    target = root / 'current/agent-review'
    check_links(home, target)
    for link in skill_links(home):
        if not link.is_symlink():
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target, target_is_directory=True)
    temporary = root / ('.current-' + uuid.uuid4().hex)
    try:
        temporary.symlink_to(release, target_is_directory=True)
        os.replace(temporary, root / 'current')
    finally:
        temporary.unlink(missing_ok=True)


def update(root, home, automatic=False):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = selected_skill(root)
        stamp = root / 'last-check'
        if automatic and current is not None and stamp.is_file() and 0 <= time.time() - stamp.stat().st_mtime < INTERVAL:
            print('Update check already attempted within the last 24 hours.')
            return current
        check_links(home, root / 'current/agent-review')
        stamp.write_text(str(time.time()) + '\n', encoding='utf-8')
        print('Checking the latest stable agent-review release...', flush=True)
        with tempfile.TemporaryDirectory(prefix='.download-', dir=root) as directory:
            staging = Path(directory)
            download(RELEASES + '/latest/download/release.json', staging / 'release.json')
            manifest = json.loads((staging / 'release.json').read_text(encoding='utf-8'))
            if not isinstance(manifest, dict) or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', str(manifest.get('version', ''))):
                raise ValueError('Invalid stable release version')
            version = manifest['version']
            if not re.fullmatch(r'[0-9a-f]{64}', str(manifest.get('sha256', ''))):
                raise ValueError('Invalid release checksum')
            release = root / 'releases' / version
            if release.exists():
                if release.is_symlink() or json.loads((release / 'release.json').read_text(encoding='utf-8')) != manifest:
                    raise ValueError('Installed release differs from the published release: ' + str(release))
                if not all((release / 'agent-review' / path).is_file() for path in REQUIRED):
                    raise ValueError('Installed release is incomplete: ' + str(release))
            else:
                print('Downloading ' + version + '...', flush=True)
                archive = staging / 'agent-review.tar.gz'
                download(RELEASES + '/download/' + version + '/agent-review.tar.gz', archive)
                if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['sha256']:
                    raise ValueError('Release archive checksum mismatch')
                unpacked = staging / 'unpacked'
                extract(archive, unpacked)
                (unpacked / 'release.json').write_text(json.dumps(manifest) + '\n', encoding='utf-8')
                release.parent.mkdir(parents=True, exist_ok=True)
                unpacked.rename(release)
            activate(root, home, release)
            if current != release / 'agent-review':
                print('Installed ' + version + '. Read its SKILL.md before continuing.')
            else:
                print('Already installed: ' + version)
            return release / 'agent-review'


def manual_command(home):
    if home == Path.home():
        return INSTALL_COMMAND
    return INSTALL_COMMAND + ' -s -- --home ' + shlex.quote(str(home))


def show_skill(skill):
    print('Skill: ' + str(skill / 'SKILL.md'))
    print('Helper: ' + str(skill / 'scripts/review.py'))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Install or update SST Agent Review; no sudo or background service.')
    parser.add_argument('--check', action='store_true', help='Check this managed installation at most once every 24 hours')
    parser.add_argument('--home', type=Path, help='Install below this home directory instead of the current user home')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 9) or platform.system() not in {'Linux', 'Darwin'}:
        parser.error('Python 3.9+ on Linux or macOS is required')
    script = Path(__file__).resolve()
    root = managed_root(script) if args.check else None
    if args.check and root is None:
        print('Manual or development copy: automatic updates are disabled.')
        show_skill(script.parent.parent)
        return 0
    home = root.parents[2] if root else (args.home or Path.home()).expanduser().resolve()
    root = root or home / '.local/share/sst-agent-review'
    try:
        skill = update(root, home, args.check)
    except (OSError, ValueError, tarfile.TarError) as error:
        print('Update unavailable: ' + str(error), file=sys.stderr)
        print('Run in a normal terminal outside the agent sandbox:\n' + manual_command(home), file=sys.stderr)
        if args.check:
            show_skill(script.parent.parent)
            return 0
        return 1
    show_skill(skill)
    if not args.check:
        print('Linked for Codex and Claude Code. Start a new agent session and ask it to use SST Agent Review (sst-agent-review).')
        print('The skill checks Git, tmux, the selected runtime and Claude credentials before your first review.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
