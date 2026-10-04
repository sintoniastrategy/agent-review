import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time

from . import ReviewError, keychain
from .documents import locked


def file_source(path):
    return {'auth': 'file', 'credentials_file': str(Path(path).expanduser().resolve())}


def keychain_source(service, account):
    return {'auth': 'keychain', 'credentials_file': '', 'keychain_service': service, 'keychain_account': account}


def private_directory():
    return Path.home() / '.local/share/sst-agent-review/credentials'


def check(settings):
    agent = settings.get('agent', 'claude')
    if settings['auth'] == 'keychain':
        if not keychain.present(settings['keychain_service'], settings['keychain_account']):
            raise ReviewError('No ' + agent + ' login in macOS Keychain; sign in with ' + agent + ' first')
        return 'macOS Keychain entry present; login checked by managed ' + agent + ' before inference'
    if not Path(settings['credentials_file']).is_file():
        raise ReviewError('No ' + agent + ' credentials file at ' + settings['credentials_file'] + '; sign in with ' + agent + ' or set [' + agent + '] credentials_file')
    label = 'ChatGPT' if agent == 'codex' else 'claude.ai subscription'
    return 'credentials file present; ' + label + ' login checked by managed ' + agent + ' before inference'


class Bridge:
    def __init__(self, settings):
        self.service = settings['keychain_service']
        self.account = settings['keychain_account']
        self.cache = private_directory()
        self.directory = None
        self.expected = None
        self.last_check = 0
        identity = hashlib.sha256((self.service + '\0' + self.account).encode()).hexdigest()
        self.lock = self.cache / ('keychain-' + identity + '.lock')

    def open(self):
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = self.cache.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ReviewError('Credential storage must be a private directory owned by the current user: ' + str(self.cache))
        with locked(self.lock), keychain.Item(self.service, self.account) as item:
            self.expected = item.value
        self.directory = Path(tempfile.mkdtemp(prefix='credentials-', dir=self.cache))
        self.path = self.directory / 'credentials.json'
        self.write(self.expected)
        return str(self.path)

    def write(self, value):
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())

    def sync(self, force=False):
        if self.directory is None or not force and time.monotonic() - self.last_check < 1:
            return
        self.last_check = time.monotonic()
        try:
            local = json.loads(self.path.read_bytes())
        except (ValueError, UnicodeError):
            if not force:
                return
            raise ReviewError('Cannot read refreshed credentials; retained private bridge at ' + str(self.directory)) from None
        if local == self.expected:
            return
        if not isinstance(local, dict):
            raise ReviewError('Refreshed credentials are invalid; retained private bridge at ' + str(self.directory))
        with locked(self.lock), keychain.Item(self.service, self.account) as item:
            if item.value not in (self.expected, local):
                raise ReviewError('Keychain credentials changed in another session; original login was not overwritten; retained private bridge at ' + str(self.directory))
            if item.value != local:
                item.replace(local)
            self.expected = local

    def close(self):
        if self.directory is not None:
            self.sync(force=True)
            shutil.rmtree(self.directory)
            self.directory = None
