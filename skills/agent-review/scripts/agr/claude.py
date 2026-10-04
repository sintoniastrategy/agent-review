import os
from pathlib import Path
import platform
import pwd
import re

from . import ReviewError, credentials, keychain


DEFAULTS = 'defaults.ini'
OPTIONS = ('model', 'effort', 'auth', 'credentials_file')
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')


def account():
    name = pwd.getpwuid(os.getuid()).pw_name
    return name if re.fullmatch(r'[a-zA-Z0-9._-]+', name) else 'claude-code-user'


def authentication(repo, values):
    method = values['auth']
    if method not in ('auto', 'file', 'keychain'):
        raise ReviewError('Claude auth must be auto, file or keychain')
    selected = values['credentials_file']
    directory = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude').expanduser()
    path = Path(selected).expanduser() if selected else directory / '.credentials.json'
    if not path.is_absolute():
        path = Path(repo) / path
    source = credentials.keychain_source('Claude Code-credentials', account())
    if method == 'keychain':
        if platform.system() != 'Darwin':
            raise ReviewError('Claude keychain authentication requires macOS')
        return source
    if method == 'auto' and not selected and platform.system() == 'Darwin':
        if keychain.present(source['keychain_service'], source['keychain_account']):
            return source
    return credentials.file_source(path)
