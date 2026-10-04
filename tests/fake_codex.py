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
    print('codex-cli 0.160.0')
    raise SystemExit(0)
if sys.argv[-2:] == ['login', 'status']:
    print('Logged in using an API key' if scenario == 'paid' else 'Logged in using ChatGPT', file=sys.stderr)
    raise SystemExit(0)
if not sys.stdin.isatty() or not sys.stdout.isatty() or 'exec' in sys.argv:
    raise SystemExit(17)
if '--no-daemon' not in sys.argv or '--ask-for-approval' not in sys.argv or sys.argv[sys.argv.index('--ask-for-approval') + 1] != 'never':
    raise SystemExit(18)
config = Path(os.environ['CODEX_HOME'])
if any(key in os.environ for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'BASH_ENV', 'PYTHONPATH')):
    raise SystemExit(15)
if not (config / 'auth.json').is_file() or Path.cwd() != Path(os.environ['HOME']):
    raise SystemExit(16)
settings_text = (config / 'config.toml').read_text()
if 'forced_login_method = "chatgpt"' not in settings_text or 'features.daemon_auto_start = false' not in settings_text:
    raise SystemExit(19)


def read_input():
    settings = termios.tcgetattr(0)
    tty.setraw(0)
    try:
        print('\x1b[?2004h\r\n› Ask Codex to do anything\r\n  ? for shortcuts     100% context left\r\n', end='', flush=True)
        data = b''
        while not data.endswith(b'\x1b[201~\r'):
            data += os.read(0, 65536)
            if data in (b'\x04', b'\x03'):
                return '/exit'
    finally:
        termios.tcsetattr(0, termios.TCSANOW, settings)
        print('\x1b[?2004l', flush=True)
    return data.removeprefix(b'\x1b[200~').removesuffix(b'\x1b[201~\r').decode().replace('\r', '\n')


prompt = read_input()
output = Path(re.search(r'^Output directory: (.*)$', prompt, re.MULTILINE)[1])
source = Path(re.search(r'^Source directory: (.*)$', prompt, re.MULTILINE)[1])
helper = Path(__file__).resolve().parents[1] / 'skills/agent-review/scripts/review.py'
(output / 'progress.txt').write_text('Codex fixture is checking the snapshot')
if scenario == 'silence':
    time.sleep(30)
draft = output / 'drafts' / 'general-codex.md'
draft.write_text('Title: Codex finding\nSeverity: P2\nPath: app.py\nLine: 1\n\nEvidence from the saved snapshot')
subprocess.run([sys.executable, str(helper), 'publish', str(output), 'finding', '--file', str(draft)], check=True)
if scenario == 'wait':
    time.sleep(30)
if scenario == 'exit':
    raise SystemExit(7)
if scenario == 'change_source':
    (source / 'app.py').write_text('value = 99\n')
(output / 'report.md').write_text('Codex fixture coverage and limitations')
subprocess.run([sys.executable, str(helper), 'publish', str(output), 'finish'], check=True)
print('Codex fixture finished', flush=True)
while True:
    message = read_input()
    if message.strip() == '/exit':
        break
    print('Codex reply: ' + message, flush=True)
