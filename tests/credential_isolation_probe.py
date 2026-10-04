import json
from pathlib import Path
import sys
import time


agent, own_output, peer_output, own_host_file, peer_host_file, private_directory = sys.argv[1:]
own_output = Path(own_output)
peer_output = Path(peer_output)
(own_output / 'ready').write_text('ready')
deadline = time.monotonic() + 20
while not (peer_output / 'ready').exists():
    if time.monotonic() > deadline:
        raise AssertionError('Peer container did not start')
    time.sleep(0.05)
path = Path('/run/agr/credentials.json')
value = json.loads(path.read_text())
assert value == {'fixture': agent}, value
value['refreshed'] = True
path.write_text(json.dumps(value))
assert json.loads(path.read_text()) == value
for blocked in (own_host_file, peer_host_file, private_directory):
    assert not Path(blocked).exists(), blocked
try:
    (peer_output / 'foreign-write').write_text('unexpected')
except OSError:
    pass
else:
    raise AssertionError('Peer output is writable')
print(json.dumps({
    'own_credentials_readable': True, 'own_refresh_writable': True,
    'peer_credentials_hidden': True, 'private_storage_hidden': True,
}))
