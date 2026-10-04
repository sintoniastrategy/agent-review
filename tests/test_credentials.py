import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import Mock, patch

from .support import RepositoryTest
from agr import ReviewError, configuration, credentials, keychain, runtime
from agr.cli import execute, parser


class AgentSettingsTests(RepositoryTest):
    def command(self, *args):
        return execute(parser().parse_args(['--repo', str(self.repo), *args]))

    def test_agent_sections_preserve_their_own_values_across_layers_and_cli_selection(self):
        self.write_settings({'runtime': 'native', 'claude': {'model': 'sonnet', 'auth': 'keychain'},
            'codex': {'model': 'gpt-6.1-sol', 'effort': 'medium', 'auth': 'file'}}, self.global_config)
        self.write_settings({'agent': 'codex', 'claude': {'effort': 'high'}, 'codex': {'credentials_file': 'codex.json'}})
        codex = configuration.effective(self.repo)
        self.assertEqual((codex['agent'], codex['auth'], codex['effort'], codex['credentials_file']), ('codex', 'file', 'medium', 'codex.json'))
        claude = configuration.effective(self.repo, overrides={'agent': 'claude'})
        self.assertEqual((claude['model'], claude['auth'], claude['effort'], claude['credentials_file']), ('sonnet', 'keychain', 'high', ''))
        self.assertEqual(configuration.effective(self.repo, overrides={'agent': 'codex', 'effort': 'low'})['effort'], 'low')
        self.assertEqual(runtime.configuration(self.repo, overrides={'agent': 'codex'})['agent'], 'codex')

    def test_legacy_preferences_keep_their_configured_owner_when_switching_agents(self):
        self.write_settings({'model': 'sonnet', 'auth': 'keychain', 'credentials_file': '/claude/token'}, self.global_config)
        self.write_settings({'agent': 'codex', 'effort': 'medium'})
        codex = configuration.effective(self.repo)
        self.assertEqual((codex['model'], codex['auth'], codex['credentials_file'], codex['effort']), ('gpt-6.1-sol', 'auto', '', 'medium'))
        claude = configuration.effective(self.repo, overrides={'agent': 'claude'})
        self.assertEqual((claude['model'], claude['auth'], claude['credentials_file'], claude['effort']), ('sonnet', 'keychain', '/claude/token', 'xhigh'))

    def test_preflight_agent_override_and_prepare_validate_the_same_native_runtime(self):
        managed = self.fake_runtime(agent='codex')
        self.write_settings({'claude': {'auth': 'keychain'}, 'codex': {'credentials_file': managed['credentials_file']}})
        before = (self.journal.directory / 'config.ini').read_bytes()
        result = self.command('preflight', '--base', 'base', '--agent', 'codex', '--runtime', 'native', '--effort', 'medium')
        self.assertEqual((result['agent'], result['runtime'], result['auth'], result['effort']), ('codex', 'native', 'file', 'medium'))
        with patch('agr.cli.runtime.setup', return_value=managed) as setup:
            record = self.command('prepare', '--agent', 'codex', '--runtime', 'native', '--effort', 'medium')
        self.assertEqual(record['effort'], 'medium')
        for key in ('agent', 'runtime', 'auth', 'credentials_file'):
            self.assertEqual(setup.call_args.args[1][key], result[key])
        self.assertEqual((self.journal.directory / 'config.ini').read_bytes(), before)

    def test_platform_defaults_respect_explicit_runtime_selection(self):
        for system in ('Darwin', 'Linux'):
            with self.subTest(system=system), patch('agr.runtime.platform.system', return_value=system), patch('agr.keychain.present', return_value=False):
                for agent in ('claude', 'codex'):
                    self.assertEqual(runtime.configuration(self.repo, agent=agent)['runtime'], 'docker')
                    self.assertEqual(runtime.configuration(self.repo, {'runtime': 'auto'}, agent=agent)['runtime'], 'docker')
                    self.assertEqual(runtime.configuration(self.repo, {'runtime': 'native'}, agent=agent)['runtime'], 'native')

    def test_prepare_checks_tools_before_installing(self):
        settings = runtime.configuration(self.repo, {'runtime': 'native'})
        with patch('agr.runtime.shutil.which', side_effect=lambda name: None if name == 'tmux' else '/bin/' + name), patch('agr.runtime.install_binary') as install:
            with self.assertRaisesRegex(ReviewError, 'Missing required tools: tmux'):
                runtime.setup(self.repo, settings)
        install.assert_not_called()

    def test_codex_keyring_identity_uses_canonical_original_home_in_both_runtimes(self):
        home = Path(os.environ['HOME']) / '.codex'
        home.mkdir()
        alias = home.parent / 'alias'
        alias.symlink_to(home, target_is_directory=True)
        expected = 'cli|' + hashlib.sha256(str(home).encode()).hexdigest()[:16]
        with patch.dict(os.environ, {'CODEX_HOME': str(alias)}), patch('agr.runtime.platform.system', return_value='Darwin'), patch('agr.keychain.present', return_value=True):
            for mode in ('native', 'docker'):
                settings = runtime.configuration(self.repo, {'runtime': mode}, agent='codex')
                self.assertEqual((settings['auth'], settings['keychain_service'], settings['keychain_account']), ('keychain', 'Codex Auth', expected))
            (home / 'auth.json').write_text('{}')
            self.assertEqual(runtime.configuration(self.repo, agent='codex')['auth'], 'file')
            self.assertEqual(runtime.configuration(self.repo, {'codex': {'auth': 'keyring'}}, agent='codex')['auth'], 'keychain')

    def test_claude_auto_uses_file_fallback_and_respects_config_directory(self):
        profile = Path(os.environ['HOME']) / 'claude-profile'
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(profile)}), patch('agr.runtime.platform.system', return_value='Darwin'), patch('agr.keychain.present', return_value=False):
            settings = runtime.configuration(self.repo)
        self.assertEqual(settings['auth'], 'file')
        self.assertEqual(settings['credentials_file'], str(profile / '.credentials.json'))


class CredentialBridgeTests(RepositoryTest):
    def setUp(self):
        super().setUp()
        self.value = {'tokens': {'access_token': 'initial', 'refresh_token': 'initial-refresh'}}
        owner = self

        class Item:
            def __init__(self, service, account):
                owner.assertEqual((service, account), ('fixture-service', 'fixture-account'))

            def __enter__(self):
                self.value = json.loads(json.dumps(owner.value))
                return self

            def replace(self, value):
                owner.value = value

            def __exit__(self, *args):
                pass

        replacement = patch('agr.keychain.Item', Item)
        replacement.start()
        self.addCleanup(replacement.stop)
        self.bridge = credentials.Bridge({'keychain_service': 'fixture-service', 'keychain_account': 'fixture-account'}, self.journal.directory / '.cache')

    def test_private_bridge_preserves_mount_inode_and_syncs_refreshed_tokens(self):
        path = Path(self.bridge.open())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        original_inode = path.stat().st_ino
        refreshed = {'tokens': {'access_token': 'new', 'refresh_token': 'new-refresh'}}
        path.write_text(json.dumps(refreshed))
        self.bridge.sync(force=True)
        self.assertEqual(self.value, refreshed)
        self.assertEqual(path.stat().st_ino, original_inode)
        self.bridge.close()
        self.assertFalse(path.parent.exists())

    def test_conflicting_login_is_preserved_and_refreshed_file_is_retained(self):
        path = Path(self.bridge.open())
        path.write_text(json.dumps({'tokens': {'access_token': 'refreshed'}}))
        self.value = {'tokens': {'access_token': 'another-login'}}
        with self.assertRaisesRegex(ReviewError, 'another session') as error:
            self.bridge.close()
        self.assertEqual(self.value['tokens']['access_token'], 'another-login')
        self.assertTrue(path.is_file())
        self.assertNotIn('another-login', str(error.exception))

    def test_unchanged_local_file_never_overwrites_new_external_login(self):
        path = Path(self.bridge.open())
        self.value = {'tokens': {'access_token': 'another-login'}}
        self.bridge.close()
        self.assertEqual(self.value['tokens']['access_token'], 'another-login')
        self.assertFalse(path.exists())

    def test_partial_write_can_finish_before_next_sync_but_is_retained_on_close(self):
        path = Path(self.bridge.open())
        path.write_text('{')
        self.bridge.sync()
        self.assertEqual(self.value['tokens']['access_token'], 'initial')
        with self.assertRaisesRegex(ReviewError, 'retained private bridge'):
            self.bridge.close()
        self.assertTrue(path.exists())

    def test_same_update_from_another_session_is_accepted(self):
        path = Path(self.bridge.open())
        self.value = {'tokens': {'access_token': 'refreshed'}}
        path.write_text(json.dumps(self.value))
        self.bridge.close()
        self.assertFalse(path.exists())


class KeychainTests(RepositoryTest):
    def test_metadata_probe_distinguishes_missing_and_unavailable_keychain(self):
        for status, expected in ((0, True), (44, False)):
            with patch('agr.keychain.subprocess.run', return_value=subprocess.CompletedProcess([], status)) as run:
                self.assertEqual(keychain.present('service', 'account'), expected)
            self.assertNotIn('-w', run.call_args.args[0])
            self.assertNotIn('-g', run.call_args.args[0])
        with patch('agr.keychain.subprocess.run', return_value=subprocess.CompletedProcess([], 36, '', 'private')):
            with self.assertRaisesRegex(ReviewError, 'unlock') as error:
                keychain.present('service', 'account')
            self.assertNotIn('private', str(error.exception))

    def test_native_keychain_api_reads_and_updates_without_secrets_in_process_arguments(self):
        security = Mock()
        foundation = Mock()
        payload = b'{"tokens":{"access_token":"fixture"}}'
        buffer = ctypes.create_string_buffer(payload)

        def find(keychains, service_length, service, account_length, account, length, data, reference):
            self.assertEqual((service_length, service, account_length, account), (7, b'service', 7, b'account'))
            length._obj.value = len(payload)
            data._obj.value = ctypes.addressof(buffer)
            reference._obj.value = 123
            return 0

        security.SecKeychainFindGenericPassword.side_effect = find
        security.SecKeychainItemModifyAttributesAndData.return_value = 0
        with patch('agr.keychain.ctypes.CDLL', side_effect=[security, foundation]):
            with keychain.Item('service', 'account') as item:
                self.assertEqual(item.value['tokens']['access_token'], 'fixture')
                item.replace({'tokens': {'access_token': 'refreshed'}})
        security.SecKeychainItemFreeContent.assert_called_once()
        foundation.CFRelease.assert_called_once()
        call = security.SecKeychainItemModifyAttributesAndData.call_args.args
        self.assertEqual(json.loads(ctypes.string_at(call[3], call[2])), {'tokens': {'access_token': 'refreshed'}})
