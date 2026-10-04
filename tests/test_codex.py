import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import unittest
from unittest.mock import patch

from .support import RepositoryTest, TmuxTest, fake_launcher, until
from agr import ReviewError, codex, configuration, progress, reporting, runtime, tmux
from agr.cli import cancel, execute, launch_round, parser
from agr.prompts import prepare
from agr.runner import input_ready, reviewer_command


class CodexConfigurationTests(RepositoryTest):
    def command(self, *args):
        return execute(parser().parse_args(['--repo', str(self.repo), *args]))

    def test_agent_flag_selects_its_defaults_and_runtime_without_changing_ini(self):
        config = self.write_settings({'runtime': 'native', 'preset': 'lenses'})
        original = config.read_bytes()
        with patch('agr.cli.runtime.setup', return_value=self.fake_runtime(agent='codex')) as setup:
            record = self.command('prepare', '--agent', 'codex')
        self.assertEqual((record['reviewer'], record['model'], record['effort']), ('codex', 'gpt-6.1-sol', 'xhigh'))
        self.assertEqual(record['directory'], 'r01-codex1')
        self.assertEqual(setup.call_args.args[1]['agent'], 'codex')
        self.assertEqual(setup.call_args.args[1]['credentials_file'], str(Path.home() / '.codex/auth.json'))
        directory = self.journal.round_directory(record['id'])
        prompt = (directory / 'prompt.md').read_text()
        self.assertIn('three independent subagents', prompt)
        self.assertIn('Source directory: ' + str(self.repo), prompt)
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual(configuration.load_review(self.repo)['agent'], 'claude')
        self.write_settings({'agent': 'claude', 'model': 'sonnet'})
        frozen = self.journal.round(record['id'])
        args = reviewer_command(frozen, directory)
        self.assertEqual(args[args.index('--model') + 1], 'gpt-6.1-sol')
        self.assertEqual((directory / 'prompt.md').read_text(), prompt)

    def test_personal_and_local_preferences_override_agent_defaults(self):
        self.write_settings({'agent': 'codex', 'effort': 'high'}, self.global_config)
        self.assertEqual(configuration.load_review(self.repo)['model'], 'gpt-6.1-sol')
        self.write_settings({'model': 'explicit-model', 'effort': 'ultra'})
        selected = configuration.load_review(self.repo)
        self.assertEqual((selected['model'], selected['effort']), ('explicit-model', 'ultra'))
        selected = configuration.load_review(self.repo, {'model': 'one-run-model'})
        self.assertEqual(selected['model'], 'one-run-model')
        claude = configuration.load_review(self.repo, {'agent': 'claude'})
        self.assertEqual((claude['model'], claude['effort']), ('opus', 'xhigh'))
        selected = configuration.load_review(self.repo, {'agent': 'claude', 'effort': 'max'})
        self.assertEqual(selected['model'], 'opus')

    def test_preflight_uses_codex_file_auth_on_macos_without_installing(self):
        managed = self.fake_runtime(agent='codex')
        self.write_settings({'agent': 'codex', 'runtime': 'native', 'credentials_file': managed['credentials_file']})
        with patch('agr.runtime.platform.system', return_value='Darwin'), patch('agr.runtime.shutil.which', return_value='/tools/fake'), patch('agr.cli.runtime.setup') as setup:
            result = self.command('preflight', '--base', 'base')
        setup.assert_not_called()
        self.assertEqual((result['agent'], result['auth']), ('codex', 'file'))
        self.assertTrue(result['defaults'].endswith('/defaults-codex.ini'))
        self.assertIn('workspace-write', result['sandbox'])
        self.assertIn('ChatGPT', result['authentication'])
        self.assertEqual(self.journal.rows('rounds'), [])
        with patch.dict(os.environ, {'CODEX_HOME': str(self.repo / '.agr/login')}):
            settings = runtime.configuration(self.repo, {'agent': 'codex'})
        self.assertEqual(settings['credentials_file'], str(self.repo / '.agr/login/auth.json'))
        with self.assertRaisesRegex(ReviewError, 'Codex auth must be'):
            runtime.configuration(self.repo, {'agent': 'codex', 'auth': 'keychain'})

    def test_parallel_mixed_agents_keep_separate_slots_and_shared_snapshot(self):
        first = self.prepared()
        second = self.prepared(agent='codex', parallel_with=first['id'])
        third = self.prepared(agent='codex', parallel_with=first['id'])
        fourth = self.prepared(parallel_with=second['id'])
        records = (first, second, third, fourth)
        self.assertEqual([item['directory'] for item in records], ['r01-claude1', 'r01-codex1', 'r01-codex2', 'r01-claude2'])
        self.assertEqual(len({item['source']['tree'] for item in records}), 1)
        for record in records:
            directory = self.journal.round_directory(record['id'])
            self.assertEqual((directory / 'input/history.md').read_bytes(), (self.journal.round_directory(first['id']) / 'input/history.md').read_bytes())
            cancel(self.journal, record['id'])
        following = self.prepared(agent='codex')
        self.assertEqual(following['directory'], 'r02-codex1')

    def test_mismatched_runtime_never_creates_a_round(self):
        with self.assertRaisesRegex(ReviewError, 'does not match'):
            prepare(self.journal, self.fake_runtime(), agent='codex')
        self.assertEqual(self.journal.rows('rounds'), [])


class CodexRuntimeTests(RepositoryTest):
    def test_chatgpt_auth_is_required_without_exposing_failed_login_output(self):
        self.assertEqual(codex.subscription_auth(fake_launcher(agent='codex'), self.repo), {'authMethod': 'chatgpt'})
        with self.assertRaisesRegex(ReviewError, 'no API fallback'):
            codex.subscription_auth(fake_launcher('paid', agent='codex'), self.repo)
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'CODEX_API_ENDPOINT'):
            with self.subTest(key=key), patch.dict(os.environ, {key: 'private'}), patch('agr.codex.subprocess.run') as launch:
                with self.assertRaisesRegex(ReviewError, key):
                    codex.subscription_auth(['codex'], self.repo)
                launch.assert_not_called()
        for result in (subprocess.CompletedProcess([], 1, '', 'private'), subprocess.CompletedProcess([], 0, '', 'unexpected private')):
            with patch('agr.codex.subprocess.run', return_value=result):
                with self.assertRaises(ReviewError) as error:
                    codex.subscription_auth(['codex'], self.repo)
                self.assertNotIn('private', str(error.exception))

    def test_native_command_keeps_source_outside_writable_roots(self):
        record = self.prepared(agent='codex', effort='ultra')
        directory = self.journal.round_directory(record['id'])
        args = reviewer_command(record, directory)
        self.assertIn('--no-daemon', args)
        self.assertIn('--no-alt-screen', args)
        self.assertEqual(args[args.index('--sandbox') + 1], 'workspace-write')
        self.assertEqual(args[args.index('--ask-for-approval') + 1], 'never')
        self.assertEqual(args[args.index('--add-dir') + 1], str(directory / 'output'))
        self.assertNotIn(str(self.repo), args)
        self.assertIn('model_reasoning_effort="ultra"', args)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)

    def test_clean_home_uses_only_shared_auth_and_its_own_trusted_root(self):
        managed = self.fake_runtime(agent='codex')
        home = self.repo / '.agr/private home'
        home.mkdir(mode=0o700)
        codex.initialize(home, managed['credentials_file'])
        config = home / '.codex'
        self.assertEqual(config.stat().st_mode & 0o777, 0o700)
        self.assertEqual((config / 'auth.json').resolve(), Path(managed['credentials_file']))
        (config / 'auth.json').write_text('{"refreshed":true}')
        self.assertEqual(json.loads(Path(managed['credentials_file']).read_text()), {'refreshed': True})
        settings = (config / 'config.toml').read_text()
        self.assertIn('projects.' + json.dumps(str(home)) + '.trust_level = "trusted"', settings)
        self.assertIn('project_root_markers = [".agr-review-root"]', settings)
        self.assertTrue((home / '.agr-review-root').is_file())
        self.assertIn('forced_login_method = "chatgpt"', settings)
        self.assertIn('agents.max_concurrent_threads_per_session = 3', settings)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret', 'CODEX_HOME': '/host/settings', 'BASH_ENV': '/host/profile'}):
            environment = runtime.environment(home, self.repo, agent='codex')
        self.assertEqual(environment['CODEX_HOME'], str(config))
        self.assertEqual(environment['TMPDIR'], str(home / 'tmp'))
        for key in ('OPENAI_API_KEY', 'BASH_ENV', 'CLAUDE_CONFIG_DIR'):
            self.assertNotIn(key, environment)

    def test_native_setup_uses_codex_installer_and_records_the_pin(self):
        managed = self.fake_runtime(agent='codex')
        settings = runtime.configuration(self.repo, {'agent': 'codex', 'runtime': 'native', 'credentials_file': managed['credentials_file']})
        with patch('agr.codex.install_package', return_value=Path(managed['executable'])) as install, patch('agr.runtime.install_binary') as claude:
            result = runtime.setup(self.repo, settings)
        claude.assert_not_called()
        self.assertIn('/runtime/codex/' + codex.RELEASE['version'] + '/', str(install.call_args.args[0]))
        self.assertEqual(result['sha256'], managed['sha256'])
        self.assertEqual(result['package_sha256'], managed['package_sha256'])
        self.assertEqual((result['agent'], result['version']), ('codex', codex.RELEASE['version']))
        self.assertTrue(result['sandbox']['enabled'])

    def test_native_start_refuses_changed_package_before_authentication(self):
        record = self.prepared(agent='codex')
        binary = Path(record['runtime']['executable'])
        binary.with_name('unexpected-helper').write_text('changed')
        with patch('agr.codex.subscription_auth') as auth, patch('agr.runtime.os.execve') as launch:
            with self.assertRaisesRegex(ReviewError, 'package changed or is incomplete'):
                runtime.inner_review(self.journal, record['id'])
        auth.assert_not_called()
        launch.assert_not_called()

    def test_docker_build_and_launch_select_codex_without_writable_source(self):
        record = self.prepared(agent='codex')
        client = self.fake_docker_client()
        results = [json.dumps({'Server': {'Components': [{'Name': 'Engine'}]}}), json.dumps({'OSType': 'linux', 'SecurityOptions': ['name=rootless']}), '', 'sha256:' + '1' * 64]
        settings = runtime.configuration(self.repo, {'agent': 'codex', 'credentials_file': record['runtime']['credentials_file']})
        with patch('agr.runtime.docker_connection', return_value=client), patch('agr.runtime.checked', side_effect=results), patch('agr.runtime.subprocess.run') as build:
            managed = runtime.setup(self.repo, settings)
        self.assertIn('REVIEW_AGENT=codex', build.call_args.args[0])
        self.assertEqual(managed['executable'], '/opt/agr/codex/bin/codex')
        self.assertTrue(managed['image_tag'].startswith('agr-codex:' + codex.RELEASE['version']))
        record = self.journal.update_round(record['id'], runtime=managed)
        args = runtime.container_command(self.journal, record['id'])
        self.assertIn('CODEX_HOME=/tmp/agr-home/.codex', args)
        self.assertIn(runtime.mount(self.repo, self.repo, readonly=True), args)
        command = reviewer_command(record, self.journal.round_directory(record['id']))
        self.assertIn('--dangerously-bypass-approvals-and-sandbox', command)
        self.assertNotIn('--sandbox', command)
        self.assertIn('!codex-release.json', (runtime.SKILL / '.dockerignore').read_text().splitlines())


class CodexInstallerTests(RepositoryTest):
    def bundle(self, extra=None, missing=None):
        metadata = {'layoutVersion': 1, 'version': codex.RELEASE['version'], 'target': 'test-linux', 'variant': 'codex', 'entrypoint': 'bin/codex', 'resourcesDir': 'codex-resources', 'pathDir': 'codex-path'}
        files = {name: name.encode() for name in ('bin/codex', 'bin/codex-code-mode-host', 'codex-path/rg', 'codex-resources/zsh/bin/zsh', 'codex-resources/bwrap')}
        files['codex-package.json'] = json.dumps(metadata).encode()
        files.pop(missing, None)
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for name, data in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o644 if name.endswith('.json') else 0o755
                archive.addfile(member, io.BytesIO(data))
            if extra is not None:
                archive.addfile(extra)
        archive = stream.getvalue()
        return archive, {'archive': 'codex-package-test.tar.gz', 'target': 'test-linux', 'size': len(archive), 'checksum': hashlib.sha256(archive).hexdigest()}

    def install(self, destination, archive, expected):
        def download(args, **kwargs):
            Path(args[args.index('--output') + 1]).write_bytes(archive)
        with patch('agr.runtime.platform_name', return_value='test-musl'), patch.dict(codex.RELEASE['platforms'], {'test': expected}), patch('agr.codex.subprocess.run', side_effect=download) as fetch:
            binary = codex.install_package(destination)
        return binary, fetch

    def test_download_preserves_full_layout_and_verifies_every_cached_file(self):
        archive, expected = self.bundle()
        destination = self.repo / '.agr/managed/package'
        binary, fetch = self.install(destination, archive, expected)
        self.assertEqual(binary, destination / 'bin/codex')
        self.assertEqual(binary.read_bytes(), b'bin/codex')
        self.assertTrue(os.access(destination / 'bin/codex-code-mode-host', os.X_OK))
        self.assertTrue(os.access(destination / 'codex-resources/bwrap', os.X_OK))
        self.assertTrue(fetch.call_args.args[0][-1].endswith('/rust-v' + codex.RELEASE['version'] + '/codex-package-test.tar.gz'))
        _, fetch = self.install(destination, archive, expected)
        fetch.assert_not_called()
        (destination / 'bin/codex-code-mode-host').write_bytes(b'changed')
        with self.assertRaisesRegex(ReviewError, 'package checksum mismatch'):
            self.install(destination, archive, expected)
        self.assertFalse(list(destination.parent.glob('package-*/')))
        self.assertFalse(list(destination.parent.glob('download-*')))

    def test_corrupt_download_is_not_installed(self):
        archive, expected = self.bundle()
        destination = self.repo / '.agr/managed/package'
        with self.assertRaisesRegex(ReviewError, 'pinned size and SHA-256'):
            self.install(destination, b'corrupt', expected)
        self.assertEqual(list(destination.parent.iterdir()), [])

    def test_missing_runtime_helpers_never_install_a_partial_package(self):
        for missing in ('bin/codex-code-mode-host', 'codex-path/rg', 'codex-resources/bwrap', 'codex-resources/zsh/bin/zsh'):
            with self.subTest(missing=missing):
                archive, expected = self.bundle(missing=missing)
                destination = self.repo / '.agr' / missing.replace('/', '-') / 'package'
                with self.assertRaisesRegex(ReviewError, 'missing an executable'):
                    self.install(destination, archive, expected)
                self.assertFalse(destination.exists())
                self.assertFalse(list(destination.parent.glob('package-*/')))

    def test_archive_paths_links_and_duplicate_files_are_rejected(self):
        for number, (name, kind) in enumerate((('../outside', tarfile.REGTYPE), ('/outside', tarfile.REGTYPE), ('bin/link', tarfile.SYMTYPE), ('bin/link', tarfile.LNKTYPE), ('bin/fifo', tarfile.FIFOTYPE), ('bin/codex', tarfile.REGTYPE))):
            with self.subTest(name=name, kind=kind):
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = '/outside'
                archive, expected = self.bundle(extra=member)
                destination = self.repo / '.agr' / str(number) / 'package'
                with self.assertRaisesRegex(ReviewError, 'Unexpected Codex archive entry'):
                    self.install(destination, archive, expected)
                self.assertFalse(destination.exists())

    def test_missing_or_corrupt_archive_never_replaces_installed_package(self):
        archive, expected = self.bundle()
        destination = self.repo / '.agr/managed/package'
        self.install(destination, archive, expected)
        original = codex.package_digest(destination)
        cached = destination.with_name(expected['archive'])
        cached.write_bytes(b'changed')
        with self.assertRaisesRegex(ReviewError, 'archive checksum mismatch'):
            self.install(destination, archive, expected)
        cached.unlink()
        with self.assertRaisesRegex(ReviewError, 'archive is missing'):
            self.install(destination, archive, expected)
        self.assertEqual(codex.package_digest(destination), original)

    def test_package_digest_detects_missing_files_permissions_and_links(self):
        archive, expected = self.bundle()
        destination = self.repo / '.agr/managed/package'
        self.install(destination, archive, expected)
        original = codex.package_digest(destination)
        host = destination / 'bin/codex-code-mode-host'
        host.chmod(0o644)
        self.assertNotEqual(codex.package_digest(destination), original)
        host.unlink()
        self.assertNotEqual(codex.package_digest(destination), original)
        host.symlink_to(destination / 'bin/codex')
        with self.assertRaisesRegex(ReviewError, 'Unexpected Codex package entry'):
            codex.package_digest(destination)


class CodexPromptTests(unittest.TestCase):
    def test_only_empty_codex_composer_is_ready(self):
        for prefix in ('› ', '» ', '❯ ', '\u00a0›\u00a0'):
            for footer in ('', '\n? for shortcuts 100% context left', '\nGPT-6.1-Sol medium · ~'):
                self.assertTrue(input_ready(prefix + 'Ask Codex to do anything' + footer, 'codex'))
        for screen in ('Loading...', '› 1. Yes', '› /review\n100% context left', 'Quoted: Ask Codex to do anything\n100% context left', 'Ask Codex to do anything', '> Ask Codex to do anything', '› 1. Ask Codex to do anything', '› Ask Codex to do anything else'):
            self.assertFalse(input_ready(screen, 'codex'), screen)

    def test_live_startup_screen_without_context_footer_is_ready(self):
        screen = (Path(__file__).parent / 'fixtures/codex-ready.txt').read_text()
        self.assertTrue(input_ready(screen, 'codex'))


class CodexTmuxTests(TmuxTest):
    def launch(self, record, idle_timeout=300):
        return launch_round(self.journal, record['id'], idle_timeout=idle_timeout, socket=self.socket, caller_pane='test-host:0.0')

    def test_lenses_publication_and_followup_use_one_interactive_codex_session(self):
        record = self.prepared(agent='codex', preset='lenses')
        tmux.run(tmux.command(self.socket) + ['set-environment', '-g', 'OPENAI_API_KEY', 'must-not-inherit'])
        self.launch(record)
        finished = self.terminal(record['id'])
        self.assertEqual(finished['status'], 'completed', finished)
        self.assertEqual(finished['auth'], {'authMethod': 'chatgpt'})
        self.assertTrue(finished['session_open'])
        until(lambda: 'Codex fixture finished' in tmux.capture(finished))
        directory = self.journal.round_directory(record['id'])
        output = directory / 'output'
        self.assertTrue((output / 'findings/r01-codex1-f001--p2--general-codex.md').is_file())
        self.assertIn('Codex', progress.describe(self.journal, record['id']))
        original = reporting.complete(output)
        request = self.repo / '.agr/question.md'
        request.write_text('Explain the regression')
        result = execute(parser().parse_args(['--repo', str(self.repo), 'send', record['directory'], '--text-file', str(request)]))
        self.assertTrue(result['sent'])
        until(lambda: 'Codex reply: Explain the regression' in tmux.capture(finished))
        closed = tmux.close_session(self.journal, record['id'])
        self.assertEqual(closed['status'], 'completed')
        self.assertEqual(closed['finished_at'], finished['finished_at'])
        self.assertFalse(closed['session_open'])
        self.assertEqual(reporting.complete(output), original)
        self.assertIn('Codex reply: Explain the regression', (directory / 'screen.txt').read_text())
        self.assertFalse(list((self.journal.directory / '.cache').glob('runtime-*')))
        following = self.prepared(agent='codex', scope='changes')
        self.assertEqual(following['previous_review'], record['id'])
        self.assertIn('r01-codex1-f001', (self.journal.round_directory(following['id']) / 'input/history.md').read_text())

    def test_failed_auth_or_early_exit_retains_only_real_publications(self):
        for scenario, count in (('paid', 0), ('exit', 1)):
            with self.subTest(scenario=scenario):
                record = self.prepared(scenario, agent='codex')
                self.launch(record)
                finished = self.terminal(record['id'])
                self.assertEqual(finished['status'], 'failed', finished)
                self.assertEqual(len(self.journal.findings()), count)
                output = self.journal.round_directory(record['id']) / 'output'
                self.assertFalse((output / 'complete.json').exists())
                if scenario == 'paid':
                    self.assertNotIn('prompt_sent_at', finished)

    def test_cancelled_codex_does_not_close_a_completed_claude_peer(self):
        first = self.prepared()
        second = self.prepared('wait', agent='codex', parallel_with=first['id'])
        self.launch(first)
        finished = self.terminal(first['id'])
        self.assertEqual(finished['status'], 'completed', finished)
        self.launch(second)
        until(lambda: len(self.journal.findings()) == 2)
        cancel(self.journal, second['id'])
        self.assertEqual(self.terminal(second['id'])['status'], 'interrupted')
        self.assertTrue(tmux.alive(finished))
        self.assertTrue(self.journal.round(first['id'])['session_open'])

    def test_stalled_and_source_changed_runs_are_not_successful(self):
        for scenario, expected in (('silence', 'stalled'), ('change_source', 'source_changed')):
            with self.subTest(scenario=scenario):
                record = self.prepared(scenario, agent='codex')
                self.launch(record, idle_timeout=0.4 if scenario == 'silence' else 300)
                finished = self.terminal(record['id'])
                self.assertEqual(finished['status'], expected, finished)
                self.assertFalse(finished['session_open'])
