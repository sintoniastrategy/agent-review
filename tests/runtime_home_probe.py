import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from agr import read_json, runtime, write_json
from agr.store import Journal


journal = Journal(sys.argv[1], readonly=True)
number = int(sys.argv[2])
record = journal.round(number)
assert os.access(record['runtime']['executable'], os.R_OK | os.X_OK)
auth = {'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'test-only'}
with patch('agr.runtime.checked', return_value=record['runtime']['version']), patch('agr.runner.subscription_auth', return_value=auth), patch('agr.runtime.os.execve') as execute:
    runtime.inner_review(journal, number)
execute.assert_called_once()
home = Path(os.environ['HOME'])
config = Path(os.environ['CLAUDE_CONFIG_DIR'])
for directory in (home, config):
    assert directory.stat().st_uid == os.getuid()
    assert directory.stat().st_mode & 0o777 == 0o700
    write_json(directory / 'write-probe.json', {'test_only': 1})
    write_json(directory / 'write-probe.json', {'test_only': 2})
    assert read_json(directory / 'write-probe.json') == {'test_only': 2}
storage = Path(os.environ['CLAUDE_SECURESTORAGE_CONFIG_DIR'])
assert storage != config
assert not (config / '.credentials.json').exists()
assert read_json(storage / '.credentials.json') == {'test_only': True}
with (storage / '.credentials.json').open('r+') as credentials:
    value = credentials.read()
    credentials.seek(0)
    credentials.write(value)
assert read_json(home / '.claude.json')['hasCompletedOnboarding']
assert read_json(config / '.claude.json') == read_json(home / '.claude.json')
assert not (config / 'settings.json').exists()
assert read_json(journal.round_directory(number) / 'output' / '.runtime.json')['auth'] == auth
(config / 'projects').mkdir()
print(json.dumps({'uid': os.getuid(), 'private_home': True, 'state_writable': True, 'credentials_shared': True, 'model_launched': False}))
