import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

from .support import RepositoryTest
from agr import ReviewError
from agr import runtime
from agr import read_json


class RuntimeTests(RepositoryTest):
    def test_docker_user_selection_for_supported_modes(self):
        version = {'Server': {'Components': [{'Name': 'Engine'}]}}
        for security, mode, user in ((['name=seccomp,profile=builtin'], 'rootful', '1234:2345'), (['name=rootless', 'name=cgroupns'], 'rootless', '0:0')):
            with self.subTest(mode=mode):
                info = {'OSType': 'linux', 'SecurityOptions': security}
                with patch('agr.runtime.shutil.which', return_value='/usr/bin/docker'), patch('agr.runtime.checked', side_effect=[json.dumps(version), json.dumps(info)]), patch('agr.runtime.os.getuid', return_value=1234), patch('agr.runtime.os.getgid', return_value=2345):
                    self.assertEqual(runtime.docker_identity(self.fake_docker_client()), {'docker_mode': mode, 'user': user})

    def test_unsupported_engines_and_namespaces_fail_before_image_operations(self):
        version = {'Server': {'Components': [{'Name': 'Engine'}]}}
        info = {'OSType': 'linux', 'SecurityOptions': ['name=seccomp,profile=builtin']}
        cases = [
            (version, {**info, 'SecurityOptions': ['name=userns']}, 'userns-remap'),
            (version, {**info, 'SecurityOptions': ['name=rootless', 'name=userns']}, 'userns-remap'),
            ({'Server': {'Components': [{'Name': 'Podman Engine'}]}}, info, 'Podman'),
            ({}, {'host': {}, 'store': {}}, 'Podman'),
            ({'Server': {}}, info, 'Unknown container engine'),
            (version, {**info, 'OSType': 'windows'}, 'Unknown container engine'),
            (version, {'OSType': 'linux'}, 'Cannot determine Docker'),
            (version, {**info, 'SecurityOptions': ['unexpected']}, 'Cannot determine Docker'),
            ([], info, 'Cannot identify'),
        ]
        credentials = self.repo / '.agr' / 'test-credentials.json'
        credentials.write_text('{}')
        settings = runtime.configuration(self.repo, {'credentials_file': str(credentials)})
        for server, details, message in cases:
            with self.subTest(message=message, info=details):
                with patch('agr.runtime.docker_connection', return_value=self.fake_docker_client()), patch('agr.runtime.checked', side_effect=[json.dumps(server), json.dumps(details)]) as check, patch('agr.runtime.subprocess.run') as build:
                    with self.assertRaisesRegex(ReviewError, message):
                        runtime.setup(self.repo, settings)
                self.assertEqual(check.call_count, 2)
                build.assert_not_called()

    def test_podman_only_installation_is_not_used_as_a_fallback(self):
        with patch('agr.runtime.shutil.which', side_effect=[None, '/usr/bin/podman']), patch('agr.runtime.checked') as check:
            with self.assertRaisesRegex(ReviewError, 'Podman is not supported'):
                runtime.docker_identity()
            check.assert_not_called()

    def test_invalid_engine_metadata_is_rejected(self):
        with patch('agr.runtime.shutil.which', return_value='/usr/bin/docker'), patch('agr.runtime.checked', return_value='invalid JSON'):
            with self.assertRaisesRegex(ReviewError, 'Cannot identify'):
                runtime.docker_identity(self.fake_docker_client())

    def docker_record(self):
        record = self.prepared()
        values = {**record["runtime"], "mode": "docker", "docker_client": self.fake_docker_client(), "image": "sha256:" + "1" * 64, "user": "0:0", "executable": "/opt/agr/bin/claude", "python": "/usr/local/bin/python3", "entry": "/opt/agr/scripts/review.py"}
        return self.journal.update_round(record["id"], runtime=values)

    def test_container_mounts_only_source_git_reviewer_output_and_credentials(self):
        record = self.docker_record()
        args = runtime.container_command(self.journal, record["id"])
        mounts = [dict(part.split("=", 1) if "=" in part else (part, True) for part in next(csv.reader([args[index + 1]]))) for index, value in enumerate(args) if value == "--mount"]
        source = next(item for item in mounts if item["source"] == str(self.repo))
        self.assertTrue(source["readonly"])
        journal = next(item for item in mounts if item["source"] == str(self.journal.round_directory(record["id"]) / "output"))
        self.assertNotIn("readonly", journal)
        credentials = next(item for item in mounts if item["source"] == record["runtime"]["credentials_file"])
        self.assertEqual(credentials["target"], "/run/agr/credentials.json")
        self.assertEqual(len(mounts), 3)
        self.assertIn("--tty", args)
        self.assertIn("--rm", args)
        self.assertIn("--read-only", args)
        self.assertNotIn(str(Path.home() / ".claude"), [item["source"] for item in mounts])

    def test_bind_mount_quotes_commas_and_spaces(self):
        encoded = runtime.mount("/path/with, comma", "/target with space", True)
        self.assertEqual(next(csv.reader([encoded])), ["type=bind", "source=/path/with, comma", "target=/target with space", "readonly"])

    def test_native_home_is_private_and_credentials_remain_linked(self):
        record = self.prepared()
        home = self.journal.directory / 'runtime-home'
        config = home / '.claude'
        auth = {'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'test-only'}
        with patch.dict(os.environ, {'HOME': str(home), 'CLAUDE_CONFIG_DIR': str(config)}), patch('agr.runtime.checked', return_value=record['runtime']['version']), patch('agr.runner.subscription_auth', return_value=auth), patch('agr.runtime.os.execve') as execute:
            runtime.inner_review(self.journal, record['id'])
        for directory in (home, config):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(directory.stat().st_uid, os.getuid())
        credentials = config / '.credentials.json'
        self.assertTrue(credentials.is_symlink())
        self.assertEqual(credentials.resolve(), Path(record['runtime']['credentials_file']))
        self.assertEqual(read_json(credentials), {'test_only': True})
        self.assertFalse((config / 'settings.json').exists())
        self.assertTrue(read_json(home / '.claude.json')['hasCompletedOnboarding'])
        self.assertEqual(read_json(config / '.claude.json'), read_json(home / '.claude.json'))
        args = execute.call_args.args[1]
        settings = json.loads(args[args.index('--settings') + 1])
        self.assertTrue(settings['disableAllHooks'])
        self.assertTrue(settings['permissions']['blockReadsOutsideWorkingDirectories'])
        self.assertNotIn('ANTHROPIC_API_KEY', execute.call_args.args[2])

    def test_native_macos_uses_default_keychain_with_a_separate_clean_profile(self):
        record = self.prepared()
        record = self.journal.update_round(record['id'], runtime={**record['runtime'], 'auth': 'keychain', 'keychain_account': 'fixture-user'})
        home = self.journal.directory / 'keychain-home'
        config = home / '.claude'
        auth = {'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'test-only'}
        with patch.dict(os.environ, runtime.environment(home, self.repo), clear=True), patch('agr.runtime.checked', return_value=record['runtime']['version']), patch('agr.runner.subscription_auth', return_value=auth) as check, patch('agr.runtime.os.execve') as execute:
            runtime.inner_review(self.journal, record['id'])
            self.assertEqual(os.environ['CLAUDE_SECURESTORAGE_CONFIG_DIR'], '')
            self.assertEqual(os.environ['USER'], 'fixture-user')
        self.assertFalse((config / '.credentials.json').exists())
        self.assertEqual(execute.call_args.args[2]['CLAUDE_CONFIG_DIR'], str(config))
        self.assertIn('--setting-sources', check.call_args.args[0])
        self.assertNotIn('fixture-user', (self.journal.round_directory(record['id']) / 'output/.runtime.json').read_text())

    def test_macos_keychain_can_be_selected_for_native_and_docker(self):
        with patch('agr.runtime.platform.system', return_value='Darwin'), patch('agr.keychain.present', return_value=True):
            for values, expected in (({'runtime': 'native'}, 'keychain'), ({}, 'keychain'), ({'runtime': 'docker'}, 'keychain'), ({'runtime': 'native', 'credentials_file': '/explicit/credentials'}, 'file')):
                with self.subTest(values=values):
                    self.assertEqual(runtime.auth_source(runtime.configuration(self.repo, values)), expected)
            self.assertEqual(runtime.configuration(self.repo, {'runtime': 'docker', 'auth': 'keychain'})['auth'], 'keychain')
        with patch('agr.runtime.platform.system', return_value='Linux'):
            with self.assertRaisesRegex(ReviewError, 'requires macOS'):
                runtime.configuration(self.repo, {'runtime': 'native', 'auth': 'keychain'})

    def test_claude_docker_uses_keychain_bridge_file_without_native_keychain_access(self):
        record = self.prepared()
        self.journal.update_round(record['id'], runtime={**record['runtime'], 'mode': 'docker',
            'auth': 'keychain', 'credential_transport': 'file', 'keychain_account': 'fixture-user'})
        home = self.journal.directory / 'bridge-home'
        auth = {'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'test-only'}
        with patch.dict(os.environ, runtime.environment(home, self.repo), clear=True), patch('agr.runtime.checked', return_value=record['runtime']['version']), patch('agr.runner.subscription_auth', return_value=auth), patch('agr.runtime.os.execve') as execute:
            runtime.inner_review(self.journal, record['id'])
        self.assertEqual(os.readlink(home / '.claude/.credentials.json'), runtime.CONTAINER_CREDENTIALS)
        self.assertNotIn('CLAUDE_SECURESTORAGE_CONFIG_DIR', execute.call_args.args[2])

    def test_keychain_preflight_reads_metadata_without_exporting_tokens(self):
        settings = {'runtime': 'native', 'auth': 'keychain'}
        with patch('agr.runtime.keychain_account', return_value='fixture-user'), patch('agr.runtime.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as invoke:
            self.assertIn('Keychain', runtime.check_credentials(settings))
        args = invoke.call_args.args[0]
        self.assertEqual(args[0], '/usr/bin/security')
        self.assertNotIn('-w', args)
        self.assertNotIn('-g', args)
        with patch('agr.runtime.subprocess.run', return_value=subprocess.CompletedProcess([], 44)):
            with self.assertRaisesRegex(ReviewError, 'Keychain'):
                runtime.check_credentials(settings)

    def test_native_sandbox_and_fallback_do_not_install_dependencies(self):
        with patch('agr.runtime.platform.system', return_value='Linux'), patch('agr.runtime.shutil.which', side_effect=lambda name, **kw: '/usr/bin/' + name if name == 'bwrap' else None):
            sandbox = runtime.native_sandbox()
        self.assertFalse(sandbox['enabled'])
        self.assertIn('socat', sandbox['message'])
        with patch('agr.runtime.platform.system', return_value='Darwin'), patch('agr.runtime.shutil.which', return_value='/usr/bin/sandbox-exec') as lookup:
            self.assertTrue(runtime.native_sandbox()['enabled'])
            self.assertIn('/opt/homebrew/bin', lookup.call_args.kwargs['path'])

    def test_native_settings_allow_research_without_permission_prompts(self):
        settings = runtime.claude_settings({'mode': 'native', 'sandbox': {'enabled': True}, 'read_directories': ['/skill', '/git']})
        self.assertEqual(settings['permissions']['additionalDirectories'], ['/skill', '/git'])
        self.assertTrue(settings['permissions']['blockReadsOutsideWorkingDirectories'])
        self.assertTrue(settings['sandbox']['enabled'])
        self.assertTrue(settings['sandbox']['autoAllowBashIfSandboxed'])
        self.assertFalse(settings['sandbox']['failIfUnavailable'])
        self.assertFalse(settings['sandbox']['allowUnsandboxedCommands'])
        self.assertEqual(settings['sandbox']['network']['allowedDomains'], ['*'])
        self.assertNotIn('allow', settings['permissions'])
        self.assertEqual(runtime.claude_settings({'mode': 'docker'}), {'disableAllHooks': True})

    def test_macos_environment_uses_system_and_homebrew_tools_without_host_profile(self):
        with patch('agr.runtime.platform.system', return_value='Darwin'), patch.dict(os.environ, {'PATH': '/user/wrappers', 'CLAUDE_SECURESTORAGE_CONFIG_DIR': '/user/profile'}):
            environment = runtime.environment('/fresh', self.repo)
        self.assertIn('/opt/homebrew/bin', environment['PATH'])
        self.assertNotIn('/user/wrappers', environment['PATH'])
        self.assertNotIn('CLAUDE_SECURESTORAGE_CONFIG_DIR', environment)
        self.assertEqual(environment['LANG'], 'en_US.UTF-8')

    def test_clean_environment_does_not_inherit_host_configuration(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "host-key", "CLAUDE_CONFIG_DIR": "/host", "BASH_ENV": "/host.sh", "PYTHONPATH": "/host/python"}):
            values = runtime.environment(self.repo / ".agr" / "home", self.repo)
        self.assertNotIn("ANTHROPIC_API_KEY", values)
        self.assertNotIn("BASH_ENV", values)
        self.assertNotIn("PYTHONPATH", values)
        self.assertNotIn("CLAUDE_CODE_DISABLE_AUTO_MEMORY", values)
        self.assertEqual(values["CLAUDE_CONFIG_DIR"], str(self.repo / ".agr" / "home" / ".claude"))

    def test_native_install_uses_pinned_digest_and_rejects_changed_cache(self):
        destination = self.repo / ".agr" / "runtime" / "claude"
        data = b"test-only native executable"
        expected = {"checksum": hashlib.sha256(data).hexdigest(), "size": len(data)}
        def download(args, **kwargs):
            Path(args[args.index("--output") + 1]).write_bytes(data)
            return subprocess.CompletedProcess(args, 0)
        with patch("agr.runtime.platform_name", return_value="test"), patch.dict(runtime.RELEASE["platforms"], {"test": expected}), patch("agr.runtime.subprocess.run", side_effect=download) as invocation:
            self.assertEqual(runtime.install_binary(destination), destination)
            self.assertEqual(destination.read_bytes(), data)
            runtime.install_binary(destination)
            self.assertEqual(invocation.call_count, 1)
            destination.write_bytes(b"changed")
            with self.assertRaisesRegex(ReviewError, "checksum"):
                runtime.install_binary(destination)
            self.assertEqual(invocation.call_count, 1)

    def test_bad_download_is_not_installed(self):
        destination = self.repo / ".agr" / "runtime" / "claude"
        with patch("agr.runtime.platform_name", return_value="linux-x64"), patch("agr.runtime.subprocess.run"):
            with self.assertRaisesRegex(ReviewError, "SHA-256"):
                runtime.install_binary(destination)
        self.assertFalse(destination.exists())
        self.assertFalse(list(destination.parent.iterdir()))

    def test_cleanup_refuses_a_container_with_a_different_owner(self):
        record = self.docker_record()
        result = subprocess.CompletedProcess([], 0, json.dumps({'Id': 'a' * 64, 'Config': {'Labels': {'agr.session': 'another-session'}}}), '')
        with patch("agr.runtime.subprocess.run", return_value=result) as invoke:
            with self.assertRaisesRegex(ReviewError, "ownership"):
                runtime.cleanup_container(record)
            self.assertEqual(invoke.call_count, 1)

    def test_cleanup_removes_owned_container_and_accepts_auto_removal(self):
        record = self.docker_record()
        client = record['runtime']['docker_client']
        owned = subprocess.CompletedProcess([], 0, json.dumps({'Id': 'a' * 64, 'Config': {'Labels': {'agr.session': record['session_id']}}}), '')
        removed = subprocess.CompletedProcess([], 0, 'a' * 64, '')
        with patch("agr.runtime.subprocess.run", side_effect=[owned, removed]) as invoke:
            runtime.cleanup_container(record)
            self.assertEqual(invoke.call_args.args[0], client['command'] + ['container', 'rm', '--force', 'a' * 64])
            self.assertTrue(all(call.kwargs['env'] == client['environment'] for call in invoke.call_args_list))
        gone = subprocess.CompletedProcess([], 1, "", "Error: No such container: agr")
        with patch("agr.runtime.subprocess.run", return_value=gone) as invoke:
            runtime.cleanup_container(record)
            self.assertEqual(invoke.call_count, 1)
        with patch('agr.runtime.subprocess.run', side_effect=[owned, gone]):
            runtime.cleanup_container(record)

    def test_cleanup_errors_are_not_treated_as_absent_containers(self):
        record = self.docker_record()
        owned = subprocess.CompletedProcess([], 0, json.dumps({'Id': 'a' * 64, 'Config': {'Labels': {'agr.session': record['session_id']}}}), '')
        failed = subprocess.CompletedProcess([], 1, '', 'Cannot connect to the Docker daemon')
        for results in ([failed], [owned, failed]):
            with self.subTest(results=len(results)), patch('agr.runtime.subprocess.run', side_effect=results):
                with self.assertRaisesRegex(ReviewError, 'Cannot connect'):
                    runtime.cleanup_container(record)

    def test_connection_freezes_endpoint_and_tls_without_saving_host_secrets(self):
        certificates = self.repo / '.agr/tls/docker'
        metadata = {'Endpoints': {'docker': {'Host': 'tcp://docker.example:2376', 'SkipTLSVerify': False}}, 'TLSMaterial': {'docker': ['ca.pem', 'cert.pem', 'key.pem']}, 'Storage': {'TLSPath': str(certificates.parent)}}
        environment = {'HOME': str(self.repo), 'PATH': '/usr/bin', 'DOCKER_CONTEXT': 'selected', 'DOCKER_HOST': 'unix:///wrong.sock', 'ANTHROPIC_API_KEY': 'must-not-copy', 'DOCKER_CONFIG': str(self.repo / '.agr/docker-config'), 'SSH_AUTH_SOCK': '/saved/ssh.sock', 'DOCKER_API_VERSION': '1.44'}
        with patch.dict(os.environ, environment, clear=True), patch('agr.runtime.shutil.which', return_value='/usr/bin/docker'), patch('agr.runtime.checked', return_value=json.dumps(metadata)) as inspect:
            client = runtime.docker_connection()
        self.assertEqual(inspect.call_args.args[0], ['/usr/bin/docker', 'context', 'inspect', 'selected', '--format', '{{json .}}'])
        self.assertEqual(client['endpoint'], 'tcp://docker.example:2376')
        self.assertIn('--tlsverify', client['command'])
        self.assertEqual(client['command'][client['command'].index('--tlskey') + 1], str(certificates / 'key.pem'))
        for key in ('ANTHROPIC_API_KEY', 'DOCKER_CONTEXT', 'DOCKER_HOST'):
            self.assertNotIn(key, client['environment'])
        self.assertEqual(client['environment']['SSH_AUTH_SOCK'], '/saved/ssh.sock')
        self.assertEqual(client['environment']['DOCKER_API_VERSION'], '1.44')

    def test_default_context_and_explicit_host_are_resolved_before_tmux(self):
        for host in ('', 'unix:///selected.sock'):
            with self.subTest(host=host):
                endpoint = host or 'unix:///default.sock'
                metadata = json.dumps({'Endpoints': {'docker': {'Host': endpoint}}})
                responses = [metadata] if host else ['saved-context', metadata]
                with patch.dict(os.environ, {'HOME': str(self.repo), 'DOCKER_HOST': host}, clear=True), patch('agr.runtime.shutil.which', return_value='/usr/bin/docker'), patch('agr.runtime.checked', side_effect=responses):
                    client = runtime.docker_connection()
                self.assertEqual(client['command'][-2:], ['--host', endpoint])
                self.assertNotIn('--tls', client['command'])

    def test_default_tls_settings_freeze_certificate_paths_and_verification(self):
        certificates = self.repo / '.agr/certificates'
        certificates.mkdir()
        (certificates / 'ca.pem').write_text('test CA')
        metadata = json.dumps({'Endpoints': {'docker': {'Host': 'tcp://docker.example:2376'}}})
        for verify in ('', '1'):
            environment = {'HOME': str(self.repo), 'DOCKER_HOST': 'tcp://docker.example:2376', 'DOCKER_TLS': '1', 'DOCKER_TLS_VERIFY': verify, 'DOCKER_CERT_PATH': str(certificates)}
            with self.subTest(verify=verify), patch.dict(os.environ, environment, clear=True), patch('agr.runtime.shutil.which', return_value='/usr/bin/docker'), patch('agr.runtime.checked', return_value=metadata):
                client = runtime.docker_connection()
            self.assertIn('--tlsverify' if verify else '--tls', client['command'])
            self.assertEqual(client['command'][client['command'].index('--tlscacert') + 1], str(certificates / 'ca.pem'))
            self.assertEqual(client['command'][client['command'].index('--tlscert') + 1], '')
            self.assertEqual(client['command'][client['command'].index('--tlskey') + 1], '')
            self.assertNotIn('DOCKER_TLS_VERIFY', client['environment'])

    def test_context_without_tls_material_does_not_inherit_default_certificates(self):
        metadata = json.dumps({'Endpoints': {'docker': {'Host': 'tcp://docker.example:2376', 'SkipTLSVerify': True}}})
        with patch.dict(os.environ, {'HOME': str(self.repo), 'DOCKER_CONTEXT': 'selected', 'DOCKER_TLS_VERIFY': '1'}, clear=True), patch('agr.runtime.shutil.which', return_value='/usr/bin/docker'), patch('agr.runtime.checked', return_value=metadata):
            client = runtime.docker_connection()
        self.assertIn('--tls', client['command'])
        self.assertNotIn('--tlsverify', client['command'])
        for flag in ('--tlscacert', '--tlscert', '--tlskey'):
            self.assertEqual(client['command'][client['command'].index(flag) + 1], '')

    def test_setup_uses_the_saved_client_for_detection_build_and_inspection(self):
        credentials = self.repo / '.agr/credentials.json'
        credentials.write_text('{}')
        client = self.fake_docker_client()
        server = {'Server': {'Components': [{'Name': 'Engine'}]}}
        info = {'OSType': 'linux', 'SecurityOptions': ['name=rootless']}
        results = [json.dumps(server), json.dumps(info), '', 'sha256:' + '1' * 64]
        with patch('agr.runtime.docker_connection', return_value=client), patch('agr.runtime.checked', side_effect=results) as inspect, patch('agr.runtime.subprocess.run') as build:
            managed = runtime.setup(self.repo, runtime.configuration(self.repo, {'credentials_file': str(credentials)}))
        self.assertEqual(managed['docker_client'], client)
        for call in inspect.call_args_list + build.call_args_list:
            self.assertEqual(call.args[0][:len(client['command'])], client['command'])
            self.assertEqual(call.kwargs['env'], client['environment'])

    def test_missing_connection_is_not_replaced_with_current_docker_defaults(self):
        record = self.docker_record()
        del record['runtime']['docker_client']
        with patch('agr.runtime.subprocess.run') as invoke:
            with self.assertRaisesRegex(ReviewError, 'not recorded'):
                runtime.cleanup_container(record)
            invoke.assert_not_called()
