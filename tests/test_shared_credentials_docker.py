import base64
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import unittest

from .support import RepositoryTest, ROOT, SCRIPTS
from agr import runtime, write_json
from agr import now


@unittest.skipUnless(os.environ.get('AGR_TEST_DOCKER_IMAGE'), 'Set AGR_TEST_DOCKER_IMAGE to an existing local image')
class SharedCredentialsDockerTests(RepositoryTest):
    def setUp(self):
        self.client = runtime.docker_connection()
        self.identity = runtime.docker_identity(self.client)
        super().setUp()

    def response(self, process):
        ready, _, _ = select.select([process.stdout], [], [], 15)
        self.assertTrue(ready, 'Credential probe did not respond')
        line = process.stdout.readline()
        self.assertTrue(line, 'Credential probe exited: ' + (process.stderr.read() if process.poll() is not None else 'no response'))
        return json.loads(line)

    def send(self, process, action, **values):
        process.stdin.write(json.dumps({'action': action, **values}) + '\n')
        process.stdin.flush()
        return self.response(process)

    def stop(self, process):
        if process.poll() is None:
            try:
                process.stdin.write('{"action":"quit"}\n')
                process.stdin.flush()
                process.wait(timeout=5)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()

    def probe(self, record, container):
        script = ROOT / 'tests/shared_credentials_probe.py'
        if container:
            image = record['runtime']['image']
            args = runtime.container_command(self.journal, record['id'])
            position = args.index(image)
            args = [argument for argument in args[:position] if argument != '--tty']
            args += ['--pull', 'never', '--network', 'none', '--entrypoint', 'python3',
                '--env', 'PYTHONPATH=/opt/agr/bundle/scripts', '--mount', runtime.mount(SCRIPTS.parent, '/opt/agr/bundle', True),
                '--mount', runtime.mount(script, '/opt/agr/probe.py', True),
                image, '-u', '/opt/agr/probe.py', str(self.journal.directory), str(record['id']), 'docker']
            environment = self.client['environment']
            self.addCleanup(runtime.cleanup_container, record)
        else:
            home = self.repo / '.agr/host-probe'
            environment = runtime.environment(home, self.repo, agent=record['reviewer'])
            environment['PYTHONPATH'] = str(SCRIPTS)
            args = [sys.executable, '-u', str(script), str(self.journal.directory), str(record['id']), 'host']
        process = subprocess.Popen(args, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, process)
        self.assertEqual(self.response(process), {'ready': True})
        return process

    def test_host_and_two_containers_share_replacements_and_native_lock_directory(self):
        for agent in ('claude', 'codex'):
            with self.subTest(agent=agent):
                first = self.prepared(agent=agent)
                second = self.prepared(agent=agent, parallel_with=first['id'])
                path = Path(first['runtime']['credentials_file'])
                (path.parent / 'settings.json').write_text('{"host-settings-marker":true}')
                (path.parent / 'config.toml').write_text('host-settings-marker = true\n')
                records = []
                for record in (first, second):
                    managed = {**record['runtime'], **self.identity, 'auth': 'file', 'mode': 'docker',
                        'docker_client': self.client, 'credentials_file': str(path), 'image': os.environ['AGR_TEST_DOCKER_IMAGE']}
                    records.append(self.journal.update_round(record['id'], runtime=managed))
                host = self.probe(records[0], False)
                containers = [self.probe(record, True) for record in records]
                probes = [host, *containers]
                for index, process in enumerate(probes):
                    value = {'synthetic-token-generation': index}
                    self.assertTrue(self.send(process, 'replace', value=value))
                    for reader in probes:
                        self.assertEqual(self.send(reader, 'read'), value)
                    self.assertEqual(json.loads(path.read_text()), value)
                self.assertTrue(self.send(containers[0], 'write', value={'synthetic-refresh': True}))
                self.assertEqual(self.send(host, 'read'), {'synthetic-refresh': True})
                path.unlink()
                write_json(path, {'synthetic-relogin': True})
                for process in probes:
                    self.assertEqual(self.send(process, 'read'), {'synthetic-relogin': True})
                if agent == 'claude':
                    for owner in probes:
                        self.assertTrue(self.send(owner, 'lock'))
                        self.assertTrue((path.parent / '.oauth_refresh.lock').is_dir())
                        for contender in probes:
                            self.assertFalse(self.send(contender, 'lock'))
                        self.assertTrue(self.send(owner, 'unlock'))
                for process in probes:
                    self.stop(process)
                    self.assertEqual(process.returncode, 0)
                for record in records:
                    self.journal.update_round(record['id'], status='completed')

    @unittest.skipUnless(os.environ.get('AGR_TEST_CODEX_IMAGE'), 'Set AGR_TEST_CODEX_IMAGE to an existing local Codex image')
    def test_pinned_clis_read_shared_credentials_with_network_disabled(self):
        for agent in ('claude', 'codex'):
            with self.subTest(agent=agent):
                record = self.prepared(agent=agent)
                image = os.environ['AGR_TEST_CODEX_IMAGE' if agent == 'codex' else 'AGR_TEST_DOCKER_IMAGE']
                executable = '/opt/agr/codex/bin/codex' if agent == 'codex' else '/opt/agr/bin/claude'
                managed = {**record['runtime'], **self.identity, 'auth': 'file', 'mode': 'docker',
                    'docker_client': self.client, 'image': image, 'executable': executable}
                record = self.journal.update_round(record['id'], runtime=managed)
                path = Path(managed['credentials_file'])
                path.unlink()
                process = self.probe(record, True)
                result = self.send(process, 'status')
                self.assertEqual(result.get('code'), 'login_required', result)
                if agent == 'claude':
                    value = {'claudeAiOauth': {'accessToken': 'synthetic-access-token', 'refreshToken': 'synthetic-refresh-token',
                        'expiresAt': int((time.time() + 86400) * 1000), 'scopes': ['user:profile', 'user:inference'],
                        'subscriptionType': 'max', 'rateLimitTier': 'default_claude_max_5x'}}
                else:
                    claims = {'sub': 'synthetic-user', 'exp': int(time.time()) + 86400,
                        'https://api.openai.com/auth': {'chatgpt_account_id': 'synthetic-account', 'chatgpt_plan_type': 'plus'}}
                    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
                    value = {'auth_mode': 'chatgpt', 'last_refresh': now(), 'tokens': {'id_token': 'e30.' + payload + '.signature',
                        'access_token': 'synthetic-access-token', 'refresh_token': 'synthetic-refresh-token', 'account_id': 'synthetic-account'}}
                write_json(path, value)
                result = self.send(process, 'status')
                self.assertEqual(result.get('authMethod'), 'chatgpt' if agent == 'codex' else 'claude.ai', result)
                path.unlink()
                result = self.send(process, 'status')
                self.assertEqual(result.get('code'), 'login_required', result)
                self.stop(process)
                self.assertEqual(process.returncode, 0)
                self.journal.update_round(record['id'], status='completed')

    def test_symlink_chain_reads_and_writes_the_physical_target_in_docker(self):
        for agent in ('claude', 'codex'):
            with self.subTest(agent=agent):
                record = self.prepared(agent=agent)
                path = Path(record['runtime']['credentials_file'])
                base = path.parent
                store, tokens, other = base / 'real/store', base / 'real/tokens', base / 'other'
                for directory in (store, tokens, other):
                    directory.mkdir(parents=True)
                (other / 'store').symlink_to(store, target_is_directory=True)
                target = tokens / 'auth.json'
                write_json(target, {'physical-target': True})
                (store / 'auth.json').symlink_to('../tokens/auth.json')
                path.unlink()
                path.symlink_to(other / 'store/auth.json')
                managed = {**record['runtime'], **self.identity, 'auth': 'file', 'mode': 'docker',
                    'docker_client': self.client, 'image': os.environ['AGR_TEST_DOCKER_IMAGE']}
                record = self.journal.update_round(record['id'], runtime=managed)
                process = self.probe(record, True)
                self.assertEqual(self.send(process, 'read'), {'physical-target': True})
                self.assertTrue(self.send(process, 'write', value={'container-write': True}))
                self.assertEqual(json.loads(target.read_text()), {'container-write': True})
                write_json(target, {'host-replacement': True})
                self.assertEqual(self.send(process, 'read'), {'host-replacement': True})
                self.stop(process)
                self.assertEqual(process.returncode, 0)
                self.journal.update_round(record['id'], status='completed')
