import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from .support import RepositoryTest, ROOT
from agr import runtime


@unittest.skipUnless(os.environ.get('AGR_TEST_DOCKER_IMAGE'), 'Set AGR_TEST_DOCKER_IMAGE to an existing local image')
class DockerRuntimeTests(RepositoryTest):
    def test_home_initialization_with_root_and_unprivileged_users(self):
        image = os.environ['AGR_TEST_DOCKER_IMAGE']
        assets = self.journal.directory / 'docker-test-assets'
        assets.mkdir()
        probe = assets / 'runtime_home_probe.py'
        shutil.copyfile(ROOT / 'tests' / 'runtime_home_probe.py', probe)
        for user in ('0:0', '1000:1000'):
            with self.subTest(user=user):
                record = self.prepared()
                managed = {**record['runtime'], 'mode': 'docker', 'docker_client': runtime.docker_connection(), 'image': image, 'user': user, 'executable': '/opt/agr/bin/claude'}
                self.journal.update_round(record['id'], runtime=managed)
                for directory in (self.repo, *self.journal.directory.rglob('*')):
                    if directory.is_dir():
                        directory.chmod(0o755)
                    elif directory.is_file():
                        directory.chmod(0o644)
                self.journal.directory.chmod(0o755)
                output = self.journal.round_directory(record['id']) / 'output'
                output.chmod(0o777)
                Path(managed['credentials_file']).chmod(0o666)
                args = runtime.container_command(self.journal, record['id'])
                position = args.index(image)
                args = [argument for argument in args[:position] if argument not in {'--interactive', '--tty'}]
                args += [
                    '--pull', 'never', '--network', 'none', '--entrypoint', 'python3',
                    '--env', 'PYTHONPATH=/opt/agr/scripts',
                    '--mount', runtime.mount(probe, '/opt/agr/runtime_home_probe.py', True),
                    image, '/opt/agr/runtime_home_probe.py', str(self.journal.directory), str(record['id']),
                ]
                result = subprocess.run(args, env=managed['docker_client']['environment'], capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout), {
                    'uid': int(user.split(':')[0]), 'private_home': True, 'state_writable': True,
                    'credentials_linked': True, 'model_launched': False,
                })
                self.journal.update_round(record['id'], status='completed')
