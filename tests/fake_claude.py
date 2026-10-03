import json
import os
from pathlib import Path
import re
import subprocess
import sys
import termios
import time
import tty


scenario = sys.argv[1]
if sys.argv[-1:] == ['--version']:
    print('2.1.284 (Claude Code)')
    raise SystemExit(0)
if sys.argv[-2:] == ['auth', 'status']:
    print(json.dumps({'loggedIn': True, 'authMethod': 'api_key' if scenario == 'paid' else 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'max'}))
    raise SystemExit(0)
if not sys.stdin.isatty() or not sys.stdout.isatty() or '-p' in sys.argv:
    raise SystemExit(17)
if scenario == 'clean_environment':
    if any(key in os.environ for key in ('ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'BASH_ENV', 'PYTHONPATH')):
        raise SystemExit(15)
    if not (Path(os.environ['CLAUDE_CONFIG_DIR']) / '.credentials.json').is_file():
        raise SystemExit(16)

def read_input():
    settings = termios.tcgetattr(0)
    tty.setraw(0)
    try:
        print('\x1b[?2004h\r\n❯\u00a0', end='', flush=True)
        data = b''
        while not data.endswith(b'\x1b[201~\r'):
            data += os.read(0, 65536)
            if data in (b'\x04', b'\x03'):
                return '/exit'
    finally:
        termios.tcsetattr(0, termios.TCSANOW, settings)
        print('\x1b[?2004l', flush=True)
    return data.removeprefix(b'\x1b[200~').removesuffix(b'\x1b[201~\r').decode().replace('\r', '\n')


print('\x1b[?1049h', end='', flush=True)
prompt = read_input()
if 'Perform a comprehensive code review' not in prompt:
    raise SystemExit(9)
output = Path(re.search(r'^Output directory: (.*)$', prompt, re.MULTILINE)[1])
helper = Path(__file__).resolve().parents[1] / 'skills' / 'agent-review' / 'scripts' / 'review.py'
(output / 'progress.txt').write_text('Checking the source and previous decisions')
print('Checking the source and previous decisions', flush=True)


def publish(kind, name, body):
    path = output / 'drafts' / name
    path.write_text(body)
    subprocess.run([sys.executable, str(helper), 'publish', str(output), kind, '--file', str(path)], check=True)


if scenario == 'silence':
    time.sleep(30)
if scenario == 'partial':
    for _ in range(15):
        print('Still working', flush=True)
        time.sleep(0.1)
for index in range(12 if scenario == 'many' else 1):
    publish('finding', 'issue-%03d.md' % index, 'Title: Finding %d\nSeverity: P1\nPath: app.py\nLine: 1\n\nFull text\nEvidence %d' % (index, index))
print('Partial review retained ✓', flush=True)
if scenario == 'wait':
    time.sleep(30)
if scenario == 'malformed':
    (output / 'drafts' / 'unfinished.md').write_text('Severity: bad\n\nUnpublished finding')
if scenario != 'no_report':
    (output / 'progress.txt').write_text('Preparing the final report')
    publish('report', 'summary.md', 'Complete general report with coverage and limitations.')
if scenario == 'exit':
    raise SystemExit(7)
if scenario == 'missing_result':
    raise SystemExit(0)
if scenario == 'change_source':
    Path('app.py').write_text('value = 99\n')
subprocess.run([sys.executable, str(helper), 'publish', str(output), 'finish'], check=True)
print('Finished ✓', flush=True)
while True:
    message = read_input()
    if message.strip() == '/exit':
        print('\x1b[?1049l', end='', flush=True)
        break
    print('Reply: ' + message, flush=True)
