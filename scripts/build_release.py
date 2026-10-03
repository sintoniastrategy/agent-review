import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile


def build(root, version, destination):
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Expected a stable version such as v0.1.0')
    root = Path(root)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    paths = subprocess.check_output([
        'git', '-C', str(root), 'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', 'skills/local-review',
    ]).decode().split('\0')
    archive = destination / 'agent-review.tar.gz'
    with archive.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as bundle:
            for name in sorted(set(filter(None, paths))):
                path = root / name
                if path.is_symlink() or not path.is_file():
                    raise ValueError('Expected a regular release file: ' + name)
                info = bundle.gettarinfo(str(path), str(Path(name).relative_to('skills')))
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ''
                info.mode = 0o644
                with path.open('rb') as source:
                    bundle.addfile(info, source)
    manifest = {'version': version, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
    (destination / 'release.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    shutil.copyfile(root / 'install.sh', destination / 'install.sh')
    shutil.copyfile(root / 'skills/local-review/scripts/install_skill.py', destination / 'install_skill.py')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build skill-only release assets without downloading or publishing anything.')
    parser.add_argument('version')
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parents[1], args.version, args.destination), indent=2))
