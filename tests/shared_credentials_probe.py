import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from agr import runtime, write_json, codex, runner, credentials
from agr.store import Journal


journal = Journal(sys.argv[1], readonly=True)
number = int(sys.argv[2])
record = journal.round(number)
agent = record['reviewer']
auth = {'authMethod': 'chatgpt'} if agent == 'codex' else {'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'test-only'}
version = 'codex-cli ' + record['runtime']['version'] if agent == 'codex' else record['runtime']['version']
shared = record['runtime']['credentials_file'] if sys.argv[3] == 'host' else runtime.credential_path(record['runtime'])
with patch('agr.runtime.credential_path', return_value=shared), patch('agr.runtime.checked', return_value=version), patch('agr.codex.subscription_auth', return_value=auth), patch('agr.runner.subscription_auth', return_value=auth), patch('agr.runtime.os.execve') as execute:
    runtime.inner_review(journal, number)
execute.assert_called_once()
home = Path(os.environ['HOME'])
shared = Path(shared)
if agent == 'claude':
    path = Path(os.environ['CLAUDE_SECURESTORAGE_CONFIG_DIR']) / '.credentials.json'
    assert path == shared
    assert Path(os.environ['CLAUDE_CONFIG_DIR']) == home / '.claude'
    assert not (home / '.claude/settings.json').exists()
    assert '--setting-sources' in execute.call_args.args[1]
else:
    path = Path(os.environ['CODEX_HOME']) / 'auth.json'
    assert path.resolve() == shared.resolve()
    config = (home / '.codex/config.toml').read_text()
    assert 'host-settings-marker' not in config
    assert 'approval_policy = "never"' in config
lock = shared.parent / '.oauth_refresh.lock'
print(json.dumps({'ready': True}), flush=True)
for line in sys.stdin:
    command = json.loads(line)
    action = command['action']
    if action == 'read':
        result = json.loads(path.read_text())
    elif action == 'status':
        try:
            launcher = [record['runtime']['executable']]
            result = codex.subscription_auth(launcher, home) if agent == 'codex' else runner.subscription_auth(launcher + ['--setting-sources', ''], home)
        except credentials.AuthenticationError as error:
            result = {'code': error.code, 'error': str(error)}
    elif action == 'write':
        path.write_text(json.dumps(command['value']))
        result = True
    elif action == 'replace':
        write_json(shared, command['value'])
        result = True
    elif action == 'lock':
        try:
            lock.mkdir()
            result = True
        except FileExistsError:
            result = False
    elif action == 'unlock':
        lock.rmdir()
        result = True
    elif action == 'quit':
        break
    else:
        raise ValueError(action)
    print(json.dumps(result), flush=True)
