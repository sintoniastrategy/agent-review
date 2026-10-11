from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from .support import RepositoryTest, ROOT
from agr import credentials, runtime


@unittest.skipUnless(os.environ.get('AGR_TEST_DOCKER_IMAGE'), 'Set AGR_TEST_DOCKER_IMAGE to an existing local image')
class DockerCredentialTests(RepositoryTest):
    def setUp(self):
        self.client = runtime.docker_connection()
        self.identity = runtime.docker_identity(self.client)
        super().setUp()

    def test_parallel_reviewers_can_access_only_their_own_bridge(self):
        image = os.environ['AGR_TEST_DOCKER_IMAGE']
        records = [self.prepared(agent='claude')]
        records.append(self.prepared(agent='codex', parallel_with=records[0]['id']))
        values = {record['reviewer']: {'fixture': record['reviewer']} for record in records}

        class Item:
            def __init__(self, service, account):
                self.account = account

            def __enter__(self):
                self.value = values[self.account]
                return self

            def replace(self, value):
                values[self.account] = value

            def __exit__(self, *args):
                pass

        replacement = patch('agr.keychain.Item', Item)
        replacement.start()
        self.addCleanup(replacement.stop)
        bridges = []
        for record in records:
            managed = {**record['runtime'], **self.identity, **credentials.keychain_source('test-only', record['reviewer']),
                'mode': 'docker', 'docker_client': self.client, 'image': image}
            bridge = credentials.Bridge(managed)
            managed['credentials_file'] = bridge.open()
            self.addCleanup(bridge.close)
            bridges.append(bridge)
            record = self.journal.update_round(record['id'], runtime=managed)
            self.addCleanup(runtime.cleanup_container, record)
        outputs = [self.journal.round_directory(record['id']) / 'output' for record in records]
        probe = (ROOT / 'tests/credential_isolation_probe.py').read_text()
        commands = []
        for index, record in enumerate(records):
            args = runtime.container_command(self.journal, record['id'])
            position = args.index(image)
            args = [argument for argument in args[:position] if argument not in {'--interactive', '--tty'}]
            args += ['--pull', 'never', '--network', 'none', '--entrypoint', 'python3', image, '-c', probe,
                record['reviewer'], str(outputs[index]), str(outputs[1 - index]), str(bridges[index].path),
                str(bridges[1 - index].path), str(credentials.private_directory())]
            commands.append(args)

        def run(args):
            return subprocess.run(args, env=self.client['environment'], capture_output=True, text=True, timeout=45)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, commands))
        for record, bridge, result in zip(records, bridges, results):
            with self.subTest(agent=record['reviewer']):
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout), {
                    'own_credentials_readable': True, 'own_refresh_writable': True,
                    'peer_credentials_hidden': True, 'private_storage_hidden': True,
                })
                bridge.close()
                self.assertEqual(values[record['reviewer']], {'fixture': record['reviewer'], 'refreshed': True})
                self.assertFalse(bridge.path.exists())
