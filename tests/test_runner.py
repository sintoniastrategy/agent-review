import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack

from .support import RepositoryTest, TmuxTest, fake_launcher, until
from agr import ReviewError
from agr.cli import cancel, launch_round
from agr.runner import claude_command, file_activity, input_ready, observe, subscription_auth, worker
from agr import tmux, progress, credentials


class InputPromptTests(unittest.TestCase):
    def test_empty_prompt_accepts_unicode_whitespace(self):
        for whitespace in ('', ' ', '\t', '\u00a0', '\u202f', '\u2003'):
            with self.subTest(whitespace=repr(whitespace)):
                screen = 'Claude Code v2.1.284\n────────\n' + whitespace + '❯' + whitespace + '\n────────\n'
                self.assertTrue(input_ready(screen))

    def test_nonempty_prompt_and_dialog_choices_are_not_ready(self):
        for screen in ('', 'Loading...', '❯\u00a0Continue', '❯ 1. Yes', 'text ❯\u00a0', '❯\u00a0/task'):
            with self.subTest(screen=screen):
                self.assertFalse(input_ready(screen))


class ActivityTests(RepositoryTest):
    def test_progress_disappearing_after_listing_does_not_abort_observation(self):
        missing = self.repo / '.agr/progress.txt'
        regular = self.repo / 'app.py'
        metadata = regular.stat()
        self.assertEqual(file_activity([missing, regular]), [(str(regular), metadata.st_size, metadata.st_mtime_ns)])

    def test_replaced_progress_uses_one_metadata_snapshot(self):
        path = self.repo / '.agr/progress.txt'
        path.write_text('first')
        metadata = path.lstat()
        def replaced(file):
            path.unlink()
            return metadata
        with patch.object(Path, 'lstat', autospec=True, side_effect=replaced) as inspect:
            activity = file_activity([path])
        inspect.assert_called_once_with(path)
        self.assertEqual(activity, [(str(path), metadata.st_size, metadata.st_mtime_ns)])

    def test_progress_symlinks_and_nonregular_files_are_not_activity(self):
        path = self.repo / '.agr/progress.txt'
        path.symlink_to(self.repo / 'app.py')
        self.assertEqual(file_activity([path, self.repo]), [])


class StartupFailureTests(RepositoryTest):
    def setUp(self):
        super().setUp()
        self.record = self.prepared()
        self.directory = self.journal.round_directory(self.record['id'])
        self.failure = self.directory / 'output/.runtime-error.json'

    def observe_exit(self):
        process = Mock(returncode=17)
        process.poll.return_value = 17
        return observe(process, self.journal, self.record['id'], self.directory, [False])

    def test_valid_startup_failures_keep_their_stage_code_and_message(self):
        cases = [('startup', 'startup_failed'), *[('authentication', code) for code in
            ('authentication_failed', 'authentication_timeout', 'login_required', 'subscription_required')]]
        for stage, code in cases:
            with self.subTest(code=code):
                self.failure.write_text(json.dumps({'stage': stage, 'code': code, 'error': 'Safe startup failure'}))
                self.assertEqual(self.observe_exit(), {'status': 'failed', 'error_stage': stage,
                    'error_code': code, 'error': 'Safe startup failure', 'exit_code': 17})

    def test_missing_malformed_or_unexpected_startup_errors_use_generic_exit(self):
        cases = [None, '{', '[]', 'null', '{}', '\udcff',
                 json.dumps({'error': 'x', 'stage': 'startup'}),
                 json.dumps({'error': {}, 'stage': 'startup', 'code': 'startup_failed'}),
                 json.dumps({'error': 'x', 'stage': [], 'code': 'startup_failed'}),
                 json.dumps({'error': 'x', 'stage': 'startup', 'code': []}),
                 json.dumps({'error': '', 'stage': 'startup', 'code': 'startup_failed'}),
                 json.dumps({'error': 'x', 'stage': 'startup', 'code': 'login_required'}),
                 json.dumps({'error': 'x', 'stage': 'runtime', 'code': 'startup_failed'}),
                 json.dumps({'error': 'x' * 70000, 'stage': 'startup', 'code': 'startup_failed'})]
        for value in cases:
            with self.subTest(value=value[:80] if value else value):
                if value is None:
                    self.failure.unlink(missing_ok=True)
                else:
                    self.failure.write_bytes(value.encode('utf-8', errors='surrogateescape'))
                result = self.observe_exit()
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['exit_code'], 17)
                self.assertIn('exited without a validated completion marker', result['error'])
                self.assertNotIn('error_code', result)

    def test_runtime_error_after_prompt_submission_is_not_a_startup_failure(self):
        (self.directory / 'output/.runtime.json').write_text(json.dumps({'auth': {}, 'version': 'test'}))
        self.failure.write_text(json.dumps({'error': 'Forged login failure', 'stage': 'authentication', 'code': 'login_required'}))
        process = Mock(returncode=17)
        process.poll.side_effect = [None, 17]
        with patch('agr.runner.tmux.capture', return_value=''), patch('agr.runner.input_ready', return_value=True), patch('agr.runner.tmux.send_file') as send, patch('agr.runner.time.sleep'):
            result = observe(process, self.journal, self.record['id'], self.directory, [False])
        send.assert_called_once()
        self.assertTrue(self.journal.round(self.record['id'])['prompt_sent_at'])
        self.assertNotIn('error_code', result)
        self.assertIn('exited without a validated completion marker', result['error'])


class WorkerCleanupTests(RepositoryTest):
    def test_keychain_bridge_is_shared_with_runtime_observers_and_closed(self):
        for agent, mode, bridged in (('codex', 'native', True), ('codex', 'docker', True), ('claude', 'docker', True), ('claude', 'native', False)):
            with self.subTest(agent=agent, mode=mode):
                record = self.prepared(agent=agent)
                managed = {**record['runtime'], 'mode': mode, 'auth': 'keychain',
                    'keychain_service': 'service', 'keychain_account': 'account', 'docker_client': self.fake_docker_client()}
                self.journal.update_round(record['id'], status='starting', launch_ready=True, runtime=managed)
                with ExitStack() as stack:
                    stack.enter_context(patch('agr.runner.sys.stdin.isatty', return_value=True))
                    stack.enter_context(patch('agr.runner.sys.stdout.isatty', return_value=True))
                    stack.enter_context(patch('agr.runner.snapshot', return_value=record['source']))
                    stack.enter_context(patch('agr.runner.runtime.container_command', return_value=['test-only-docker']))
                    launch = stack.enter_context(patch('agr.runner.subprocess.Popen'))
                    observe = stack.enter_context(patch('agr.runner.observe', return_value={'status': 'completed'}))
                    keep = stack.enter_context(patch('agr.runner.keep_session'))
                    stack.enter_context(patch('agr.runner.end_process'))
                    stack.enter_context(patch('agr.runner.runtime.cleanup_container'))
                    bridge = stack.enter_context(patch('agr.runner.credentials.Bridge'))
                    bridge.return_value.open.return_value = '/private/bridge/credentials.json'
                    launch.return_value.pid = 12345
                    self.assertEqual(worker(self.journal.directory, record['id']), 0)
                result = self.journal.round(record['id'])
                if bridged:
                    bridge.assert_called_once_with(managed)
                    bridge.return_value.close.assert_called_once()
                    self.assertEqual(result['runtime']['credentials_file'], '/private/bridge/credentials.json')
                    self.assertEqual(result['runtime']['credential_transport'], 'file')
                    self.assertIs(observe.call_args.args[-1], bridge.return_value)
                    self.assertIs(keep.call_args.args[-1], bridge.return_value)
                else:
                    bridge.assert_not_called()
                    self.assertNotIn('credential_transport', result['runtime'])
                self.assertTrue(result['cleanup_complete'])

    def test_container_cleanup_is_attempted_when_stopping_the_client_fails(self):
        record = self.prepared()
        managed = {**record['runtime'], 'mode': 'docker', 'docker_client': self.fake_docker_client()}
        self.journal.update_round(record['id'], status='starting', launch_ready=True, runtime=managed)
        with patch('agr.runner.sys.stdin.isatty', return_value=True), patch('agr.runner.sys.stdout.isatty', return_value=True), patch('agr.runner.snapshot', return_value=record['source']), patch('agr.runner.runtime.container_command', return_value=['test-only-docker']), patch('agr.runner.subprocess.Popen') as launch, patch('agr.runner.observe', return_value={'status': 'completed'}), patch('agr.runner.keep_session'), patch('agr.runner.end_process', side_effect=ReviewError('client did not stop')), patch('agr.runner.runtime.cleanup_container') as cleanup:
            launch.return_value.pid = 12345
            self.assertEqual(worker(self.journal.directory, record['id']), 0)
        cleanup.assert_called_once()
        result = self.journal.round(record['id'])
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(result['cleanup_complete'])
        self.assertIn('client did not stop', result['session_error'])


class RunnerTests(TmuxTest):
    def execute_worker(self, scenario, idle_timeout=300):
        record = self.prepared(scenario)
        launch_round(self.journal, record['id'], idle_timeout, socket=self.socket, caller_pane='test-host:0.0')
        finished = self.terminal(record['id'])
        if finished['status'] == 'completed':
            self.assertTrue(finished['session_open'])
            tmux.close_session(self.journal, record['id'])
        return self.journal.round(record['id'])

    def test_interactive_terminal_and_findings_survive_success(self):
        record = self.execute_worker('many')
        self.assertEqual(record['status'], 'completed', record)
        self.assertEqual(len(self.journal.findings()), 12)
        directory = self.journal.round_directory(record['id'])
        self.assertIn('Partial review retained', (directory / 'terminal.log').read_text())
        self.assertTrue((directory / 'output' / 'complete.json').is_file())
        self.assertTrue(record['prompt_sent_at'])
        self.assertEqual((directory / 'output' / 'progress.txt').read_text(), 'Preparing the final report')

    def test_login_required_reaches_the_journal_and_human_status_for_both_agents(self):
        for agent in ('claude', 'codex'):
            with self.subTest(agent=agent):
                record = self.prepared('login_required', agent=agent)
                launch_round(self.journal, record['id'], 300, socket=self.socket, caller_pane='test-host:0.0')
                finished = self.terminal(record['id'])
                self.assertEqual(finished['status'], 'failed')
                self.assertEqual(finished['error_code'], 'login_required')
                self.assertEqual(finished['error_stage'], 'authentication')
                self.assertEqual(finished['exit_code'], 1)
                self.assertIn(agent.capitalize() + ' login required', finished['error'])
                self.assertNotIn('prompt_sent_at', finished)
                self.assertIn('login required', progress.describe(self.journal, record['id']))
                directory = self.journal.round_directory(record['id'])
                for path in (directory / 'terminal.log', directory / 'output/.runtime-error.json'):
                    self.assertNotIn('private-token-must-not-appear', path.read_text())

    def test_auth_status_failure_preserves_exit_code_without_echoing_output(self):
        for result in (subprocess.CompletedProcess([], 7, 'private-token', 'private-token'),
                       subprocess.CompletedProcess([], 7, '{"loggedIn": true}', 'private-token')):
            with patch('agr.runner.subprocess.run', return_value=result):
                with self.assertRaises(credentials.AuthenticationError) as failure:
                    subscription_auth(['claude'], self.repo)
                self.assertIn('exit 7', str(failure.exception))
                self.assertNotIn('private-token', str(failure.exception))

    def test_failed_or_incomplete_outputs_never_count_as_success(self):
        for scenario in ('exit', 'missing_result', 'no_report'):
            with self.subTest(scenario=scenario):
                record = self.execute_worker(scenario)
                self.assertEqual(record['status'], 'failed', record)
                self.assertTrue([item for item in self.journal.findings() if item['round'] == record['id']])
                self.assertTrue((self.journal.round_directory(record['id']) / 'terminal.log').exists())

    def test_explicit_finish_keeps_unpublished_drafts_for_author_reconciliation(self):
        record = self.execute_worker('malformed')
        self.assertEqual(record['status'], 'completed', record)
        path = self.journal.round_directory(record['id']) / 'output/drafts/unfinished.md'
        self.assertEqual(path.read_text(), 'Severity: bad\n\nUnpublished finding')
        self.assertEqual(len(self.journal.findings()), 1)

    def test_source_change_invalidates_successful_result(self):
        record = self.execute_worker('change_source')
        self.assertEqual(record['status'], 'source_changed', record)
        self.assertEqual(len(self.journal.findings()), 1)

    def test_silent_process_is_stopped_without_retry(self):
        record = self.execute_worker('silence', idle_timeout=0.4)
        self.assertEqual(record['status'], 'stalled', record)
        self.assertEqual(len(self.journal.rows('rounds')), 1)

    def test_subscription_guard_refuses_paid_auth_and_key_environment(self):
        with self.assertRaises(ReviewError):
            subscription_auth(fake_launcher('paid'), self.repo)
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-only'}):
            with patch('agr.runner.subprocess.run') as invocation:
                with self.assertRaises(ReviewError):
                    subscription_auth(fake_launcher(), self.repo)
                invocation.assert_not_called()
        self.assertEqual(subscription_auth(fake_launcher(), self.repo)['authMethod'], 'claude.ai')

    def test_cancellation_retains_partial_review_and_terminates_child(self):
        record = self.prepared('wait')
        number = record['id']
        launch_round(self.journal, number, socket=self.socket, caller_pane='test-host:0.0')
        until(lambda: self.journal.findings())
        cancel(self.journal, number)
        finished = self.terminal(number)
        self.assertEqual(finished['status'], 'interrupted', finished)
        self.assertEqual(len(self.journal.findings()), 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(finished['reviewer_pid'], 0)

    def test_command_is_interactive_with_default_tools_and_no_human_input_tools(self):
        record = self.prepared()
        args = claude_command(record, self.journal.round_directory(record['id']))
        denied = set(args[args.index('--disallowedTools') + 1].split(','))
        self.assertEqual(denied, {'AskUserQuestion', 'EnterPlanMode', 'ExitPlanMode'})
        for argument in ('-p', '--print', '--output-format', '--no-session-persistence', '--include-partial-messages', '--mcp-config', '--system-prompt', '--bare', '--continue', '--resume', '--tools', '--disable-slash-commands', '--no-chrome'):
            self.assertNotIn(argument, args)
        self.assertIn('--strict-mcp-config', args)
        self.assertEqual(args[args.index('--permission-mode') + 1], 'bypassPermissions')
        self.assertEqual(args[args.index('--setting-sources') + 1], '')
        settings = json.loads(args[args.index('--settings') + 1])
        self.assertTrue(settings['disableAllHooks'])
        self.assertTrue(settings['permissions']['blockReadsOutsideWorkingDirectories'])

    def test_terminal_progress_keeps_slow_generation_alive(self):
        record = self.execute_worker('partial', idle_timeout=0.5)
        self.assertEqual(record['status'], 'completed', record)
        self.assertIn('Still working', (self.journal.round_directory(record['id']) / 'terminal.log').read_text())

    def test_managed_environment_ignores_host_auth_and_shell_configuration(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-only', 'ANTHROPIC_BASE_URL': 'https://invalid.example', 'BASH_ENV': '/nonexistent', 'PYTHONPATH': '/nonexistent'}):
            record = self.execute_worker('clean_environment')
        self.assertEqual(record['status'], 'completed', record)
        self.assertEqual(record['auth']['authMethod'], 'claude.ai')
        self.assertFalse(list((self.journal.directory / '.cache').glob('runtime-*')))

    def test_cancelled_launch_handshake_never_invokes_reviewer(self):
        record = self.prepared()
        cancel(self.journal, record['id'])
        with patch('agr.runner.subscription_auth') as auth:
            self.assertEqual(worker(self.journal.directory, record['id']), 1)
            auth.assert_not_called()

    def test_cancellation_during_startup_becomes_terminal_without_inference(self):
        record = self.prepared()
        self.journal.update_round(record['id'], status='starting', launch_ready=True)
        cancel(self.journal, record['id'])
        with patch('agr.runner.subscription_auth') as auth:
            self.assertEqual(worker(self.journal.directory, record['id']), 1)
            auth.assert_not_called()
        self.assertEqual(self.journal.round(record['id'])['status'], 'interrupted')
