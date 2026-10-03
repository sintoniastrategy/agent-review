import json
import os
from pathlib import Path
import runpy
import sys


root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'skills/local-review/scripts'))

from agr import write_json
from agr.store import Journal


directory = Path(os.environ['AGR_DOCKER_FIXTURE'])
with (directory / 'calls.jsonl').open('a') as output:
    output.write(json.dumps({'args': sys.argv[1:], 'environment': dict(os.environ)}) + '\n')
state = directory / 'container.json'
if 'run' in sys.argv:
    journal = Journal(sys.argv[-2])
    record = journal.round(int(sys.argv[-1]))
    write_json(state, {'Id': 'a' * 64, 'Config': {'Labels': {'agr.session': record['session_id']}}})
    write_json(journal.round_directory(record['id']) / 'output/.runtime.json', {
        'version': record['runtime']['version'],
        'auth': {'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'test-only'},
    })
    sys.argv = [str(root / 'tests/fake_claude.py'), 'success']
    runpy.run_path(sys.argv[0], run_name='__main__')
elif 'inspect' in sys.argv:
    if not state.exists():
        print('No such container', file=sys.stderr)
        raise SystemExit(1)
    print(state.read_text())
elif 'rm' in sys.argv:
    if sys.argv[-1] != 'a' * 64:
        raise SystemExit(2)
    state.unlink()
else:
    raise SystemExit(3)
