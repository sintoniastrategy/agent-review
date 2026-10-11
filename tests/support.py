import os
import shlex
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agent-review" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agr.prompts import prepare
from agr.store import Journal
from agr import runtime, codex, configuration


def fake_launcher(scenario="success", agent='claude'):
    return [sys.executable, str(ROOT / 'tests' / ('fake_' + agent + '.py')), scenario]


def until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for the test fixture")


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="agr-test-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "work ' tree"
        self.repo.mkdir()
        home = Path(self.temporary.name) / 'home'
        home.mkdir()
        self.global_config = home / '.local/share/sst-agent-review/config.ini'
        clean = {key: value for key, value in os.environ.items() if key not in {
            "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
            'OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'CODEX_API_ENDPOINT', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR',
            "GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE",
        }}
        clean['HOME'] = str(home)
        environment = patch.dict(os.environ, clean, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.git("init", "-q", "-b", "base")
        self.git("config", "user.name", "AGR tests")
        self.git("config", "user.email", "agr@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", "/dev/null")
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-qm", "fixture")
        self.git("switch", "-qc", "feature")
        self.journal = Journal.create(self.repo, "base", "Improve the feature; preserve the old API.")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True).stdout

    def write_settings(self, values, path=None):
        path = path or self.repo / '.agr/config.ini'
        path.parent.mkdir(parents=True, exist_ok=True)
        common = {key: value for key, value in values.items() if not isinstance(value, dict)}
        sections = {'review': common, **{key: value for key, value in values.items() if isinstance(value, dict)}}
        path.write_text('\n'.join('[' + section + ']\n' + ''.join(key + ' = ' + str(value) + '\n' for key, value in options.items()) for section, options in sections.items()), encoding='utf-8')
        return path

    def prepared(self, scenario="success", **options):
        agent = options.get('agent') or configuration.effective(self.repo)['agent']
        return prepare(self.journal, self.fake_runtime(scenario, agent=agent), **options)

    def fake_runtime(self, scenario="success", agent='claude'):
        directory = self.repo / ".agr" / "fixtures" / scenario / agent
        directory.mkdir(parents=True, exist_ok=True)
        binary = directory / 'package/bin/codex' if agent == 'codex' else directory / agent
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!" + sys.executable + "\nimport runpy\nimport sys\nsys.argv[1:1] = [" + repr(scenario) + "]\nrunpy.run_path(" + repr(str(ROOT / 'tests' / ('fake_' + agent + '.py'))) + ", run_name='__main__')\n")
        binary.chmod(0o700)
        credentials = directory / "credentials.json"
        credentials.write_text(json.dumps({"test_only": True}))
        version = codex.RELEASE['version'] if agent == 'codex' else runtime.RELEASE['version']
        managed = {"mode": "native", 'agent': agent, "version": version, "credentials_file": str(credentials), "window_name": "test/feature", "executable": str(binary), "sha256": runtime.digest(binary), "python": sys.executable, "entry": str(SCRIPTS / "review.py")}
        if agent == 'codex':
            managed['package_sha256'] = codex.package_digest(binary.parent.parent)
        return managed

    def fake_docker_client(self):
        return {'command': ['/test-only/docker', '--host', 'unix:///test-only/docker.sock'], 'environment': {'HOME': str(self.repo), 'PATH': os.defpath}, 'endpoint': 'unix:///test-only/docker.sock', 'context': 'test'}

    def running(self):
        record = self.prepared()
        self.journal.update_round(record["id"], status="running")
        return record["id"]


class TmuxTest(RepositoryTest):
    def setUp(self):
        super().setUp()
        self.socket = str(Path(self.temporary.name) / 'tmux.sock')
        holder = shlex.join([sys.executable, '-c', 'import time; time.sleep(120)'])
        subprocess.run(['tmux', '-S', self.socket, '-f', '/dev/null', 'new-session', '-d', '-s', 'test-host', '-c', str(self.repo), holder], check=True, capture_output=True)
        self.addCleanup(self.close_server)

    def close_server(self):
        from agr.cli import cancel
        from agr.store import ACTIVE
        from agr import tmux
        for record in self.journal.rows('rounds'):
            if record['status'] in {'prepared', 'preparing'} or (record['status'] in ACTIVE and record.get('worker_pid')):
                cancel(self.journal, record['id'])
                record = self.terminal(record['id'])
            if record.get('session_open'):
                tmux.close_session(self.journal, record['id'])
        subprocess.run(['tmux', '-S', self.socket, 'kill-server'], check=True, capture_output=True)

    def terminal(self, number):
        from agr.store import ACTIVE
        return until(lambda: self.journal.round(number) if self.journal.round(number)['status'] not in ACTIVE else None)
