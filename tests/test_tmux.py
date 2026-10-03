import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from unittest.mock import patch

from .support import RepositoryTest, ROOT, TmuxTest, until
from agr import ReviewError
from agr.cli import cancel, execute, launch_round, parser, recover
from agr.reporting import complete
from agr.store import ACTIVE
from agr import tmux


class TmuxTests(TmuxTest):
    def test_docker_launch_and_cleanup_ignore_the_tmux_server_environment(self):
        record = self.prepared()
        directory = self.repo / '.agr/docker-fixture'
        directory.mkdir()
        client = self.fake_docker_client()
        client['command'] = [sys.executable, str(ROOT / 'tests/fake_docker.py'), '--host', client['endpoint']]
        client['environment']['AGR_DOCKER_FIXTURE'] = str(directory)
        managed = {**record['runtime'], 'mode': 'docker', 'docker_client': client, 'image': 'test-only', 'user': '0:0'}
        self.journal.update_round(record['id'], runtime=managed)
        for key, value in (('DOCKER_HOST', 'unix:///wrong.sock'), ('DOCKER_CONTEXT', 'wrong-context'), ('ANTHROPIC_API_KEY', 'must-not-inherit')):
            tmux.run(tmux.command(self.socket) + ['set-environment', '-g', key, value])
        launch_round(self.journal, record['id'], socket=self.socket, caller_pane='test-host:0.0')
        finished = self.terminal(record['id'])
        self.assertEqual(finished['status'], 'completed', finished)
        closed = tmux.close_session(self.journal, record['id'])
        self.assertTrue(closed['cleanup_complete'])
        calls = [json.loads(line) for line in (directory / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls), 3)
        self.assertIn('run', calls[0]['args'])
        self.assertIn('inspect', calls[1]['args'])
        self.assertEqual(calls[2]['args'][-3:], ['rm', '--force', 'a' * 64])
        for call in calls:
            self.assertEqual(call['args'][:2], ['--host', client['endpoint']])
            for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'ANTHROPIC_API_KEY'):
                self.assertNotIn(key, call['environment'])
        self.assertFalse((directory / 'container.json').exists())

    def test_real_tmux_window_launches_only_the_fake_reviewer(self):
        record = self.prepared()
        result = launch_round(self.journal, record["id"], socket=self.socket, caller_pane="test-host:0.0")
        finished = self.terminal(record["id"])
        self.assertEqual(finished["status"], "completed", finished)
        self.assertEqual(result["tmux"]["socket"], self.socket)
        self.assertTrue(tmux.alive(finished))
        self.assertTrue(finished['session_open'])
        until(lambda: 'Finished ✓' in tmux.capture(finished))
        self.assertTrue(self.journal.findings())
        with self.assertRaises(ReviewError):
            launch_round(self.journal, record["id"], socket=self.socket, caller_pane="test-host:0.0")

    def test_tmux_cancellation_and_partial_report(self):
        record = self.prepared("wait")
        launch_round(self.journal, record["id"], socket=self.socket, caller_pane="test-host:0.0")
        until(lambda: self.journal.findings())
        cancel(self.journal, record["id"])
        self.assertEqual(self.terminal(record["id"])["status"], "interrupted")
        self.assertEqual(len(self.journal.findings()), 1)

    def test_readable_names_and_cleanup_of_only_owned_completed_panes(self):
        first = self.prepared()
        launch_round(self.journal, first["id"], socket=self.socket, caller_pane="test-host:0.0")
        finished = self.terminal(first["id"])
        self.assertTrue(tmux.alive(finished))
        second = self.prepared()
        result = launch_round(self.journal, second["id"], socket=self.socket, caller_pane="test-host:0.0")
        self.assertEqual(self.terminal(second["id"])["status"], "completed")
        name = tmux.run(tmux.command(self.socket) + ["display-message", "-p", "-t", result["tmux"]["window"], "#{window_name}"])
        self.assertEqual(name, "agr@test/feature-r02-claude1")
        panes = tmux.run(tmux.command(self.socket) + ["list-panes", "-a", "-F", "#{pane_id}"])
        self.assertNotIn(finished["tmux"]["pane"], panes.splitlines())
        self.assertEqual(len(self.journal.findings()), 2)
        closed = self.journal.round(first['id'])
        self.assertFalse(closed['session_open'])
        self.assertEqual(closed['finished_at'], finished['finished_at'])
        self.assertIn('Finished ✓', (self.journal.round_directory(first['id']) / 'screen.txt').read_text())

    def test_recovery_keeps_queue_after_pane_disappears(self):
        record = self.prepared()
        number = record["id"]
        output = tmux.run(tmux.command(self.socket) + ["new-window", "-d", "-P", "-F", "#{window_id}\t#{pane_id}", "-n", "lost-review", "sleep 60"])
        window, pane = output.split("\t")
        tmux.run(tmux.command(self.socket) + ["set-option", "-p", "-t", pane, "@agr_token", record["session_id"]])
        self.journal.update_round(number, status="running", tmux={"socket": self.socket, "session": "test-host", "window": window, "pane": pane})
        self.journal.add_finding(number, {"body": "Saved before crash"})
        with self.assertRaises(ReviewError):
            recover(self.journal, number)
        tmux.run(tmux.command(self.socket) + ["kill-window", "-t", window])
        self.assertEqual(recover(self.journal, number)["status"], "interrupted")
        self.assertEqual(self.journal.findings()[0]["body"], "Saved before crash")

    def test_recovery_refuses_a_potentially_orphaned_reviewer(self):
        record = self.prepared()
        self.journal.update_round(record["id"], status="running", reviewer_pid=os.getpid(), tmux={"socket": self.socket, "pane": "%999999"})
        with self.assertRaisesRegex(ReviewError, "PID still exists"):
            recover(self.journal, record["id"])

    def test_detached_session_has_attach_command_and_no_spaces_in_name(self):
        record = self.prepared()
        result = launch_round(self.journal, record['id'], socket=self.socket, caller_pane='')
        finished = self.terminal(record['id'])
        self.assertEqual(finished['status'], 'completed', finished)
        self.assertIn('attach-session', result['tmux']['attach'])
        name = tmux.run(tmux.command(self.socket) + ['display-message', '-p', '-t', result['tmux']['window'], '#{window_name}'])
        self.assertTrue(name.isascii())
        self.assertFalse(any(character.isspace() for character in name))

    def test_three_parallel_reviewers_have_separate_outputs_and_windows(self):
        first = self.prepared('partial')
        second = self.prepared('partial', parallel_with=first['id'])
        third = self.prepared('partial', parallel_with=first['id'])
        records = [first, second, third]
        for record in records:
            launch_round(self.journal, record['id'], socket=self.socket, caller_pane='test-host:0.0')
        finished = [self.terminal(record['id']) for record in records]
        self.assertEqual([item['status'] for item in finished], ['completed'] * 3, finished)
        self.assertEqual(len({item['tmux']['pane'] for item in finished}), 3)
        self.assertEqual(len({item['directory'] for item in finished}), 3)
        self.assertEqual(len(self.journal.findings()), 3)
        self.assertEqual(len({item['id'] for item in self.journal.findings()}), 3)
        self.assertEqual([item['slot'] for item in finished], [1, 2, 3])
        self.assertEqual({item['pass'] for item in finished}, {1})
        self.assertTrue(all(tmux.alive(item) for item in finished))

    def test_completed_session_accepts_followup_then_closes_without_changing_results(self):
        record = self.prepared()
        number = record['id']
        launch_round(self.journal, number, idle_timeout=0.5, socket=self.socket, caller_pane='test-host:0.0')
        finished = self.terminal(number)
        until(lambda: 'Finished ✓' in tmux.capture(finished))
        time.sleep(0.6)
        self.assertTrue(tmux.alive(finished))
        (self.repo / 'app.py').write_text('value = 2\n')
        output = self.journal.round_directory(number) / 'output'
        original = complete(output)
        request = self.repo / '.agr' / 'followup.md'
        request.write_text('Explain the first finding')
        result = execute(parser().parse_args(['--repo', str(self.repo), 'send', record['directory'], '--text-file', str(request)]))
        self.assertTrue(result['sent'])
        until(lambda: 'Reply: Explain the first finding' in tmux.capture(finished))
        self.assertEqual(complete(output), original)
        closed = execute(parser().parse_args(['--repo', str(self.repo), 'close', record['directory']]))
        self.assertEqual(closed['status'], 'completed')
        self.assertFalse(closed['session_open'])
        self.assertEqual(closed['finished_at'], finished['finished_at'])
        self.assertIsNone(tmux.pane_state(closed))
        screen = self.journal.round_directory(number) / 'screen.txt'
        self.assertIn('Reply: Explain the first finding', screen.read_text())
        self.assertEqual(complete(output), original)
        self.assertEqual(tmux.close_session(self.journal, number), closed)

    def test_starting_a_parallel_peer_keeps_the_completed_peer_open(self):
        first = self.prepared()
        second = self.prepared('wait', parallel_with=first['id'])
        launch_round(self.journal, first['id'], socket=self.socket, caller_pane='test-host:0.0')
        finished = self.terminal(first['id'])
        launch_round(self.journal, second['id'], socket=self.socket, caller_pane='test-host:0.0')
        until(lambda: len(self.journal.findings()) == 2)
        self.assertTrue(tmux.alive(finished))
        self.assertFalse(self.journal.round(first['id']).get('close_requested'))
        cancel(self.journal, second['id'])
        self.assertEqual(self.terminal(second['id'])['status'], 'interrupted')

    def test_manual_exit_after_completion_keeps_the_completed_review(self):
        record = self.prepared()
        number = record['id']
        launch_round(self.journal, number, socket=self.socket, caller_pane='test-host:0.0')
        finished = self.terminal(number)
        until(lambda: 'Finished ✓' in tmux.capture(finished))
        tmux.send_key(finished, 'C-d')
        until(lambda: not self.journal.round(number)['session_open'])
        closed = self.journal.round(number)
        self.assertEqual(closed['status'], 'completed')
        self.assertEqual(closed['finished_at'], finished['finished_at'])
        self.assertFalse(list((self.journal.directory / '.cache').glob('runtime-*')))

    def test_close_refuses_an_active_review_or_changed_ownership(self):
        record = self.prepared()
        number = record['id']
        with self.assertRaisesRegex(ReviewError, 'Cancel'):
            tmux.close_session(self.journal, number)
        launch_round(self.journal, number, socket=self.socket, caller_pane='test-host:0.0')
        finished = self.terminal(number)
        target = finished['tmux']
        base = tmux.command(self.socket)
        tmux.run(base + ['set-option', '-p', '-t', target['pane'], '@agr_token', 'different-owner'])
        try:
            with self.assertRaisesRegex(ReviewError, 'ownership'):
                tmux.close_session(self.journal, number)
            self.assertFalse(self.journal.round(number).get('close_requested'))
        finally:
            tmux.run(base + ['set-option', '-p', '-t', target['pane'], '@agr_token', finished['session_id']])

    def test_terminal_controls_only_target_an_owned_pane(self):
        record = self.prepared('wait')
        launch_round(self.journal, record['id'], socket=self.socket, caller_pane='test-host:0.0')
        until(lambda: self.journal.findings())
        current = self.journal.round(record['id'])
        target = current['tmux']
        tmux.run(tmux.command(self.socket) + ['set-option', '-p', '-t', target['pane'], '@agr_token', 'different-owner'])
        with self.assertRaisesRegex(ReviewError, 'owned'):
            tmux.send_key(current, 'C-c')
        cancel(self.journal, record['id'])
        self.assertEqual(self.terminal(record['id'])['status'], 'interrupted')


class OrphanCleanupTests(RepositoryTest):
    def orphan(self, **fields):
        record = self.prepared()
        self.journal.update_round(record['id'], status='running')
        self.journal.add_finding(record['id'], {'body': 'Retained finding'})
        managed = {**record['runtime'], 'mode': 'docker', 'docker_client': self.fake_docker_client()}
        return self.journal.update_round(record['id'], status='completed', runtime=managed, container='agr-' + record['session_id'], session_open=True, finished_at='original-finish', **fields)

    def test_missing_pane_still_cleans_owned_runtime_before_the_next_pass(self):
        first = self.orphan()
        second = self.prepared()
        with patch('agr.tmux.runtime.cleanup_container') as cleanup:
            tmux.cleanup_windows(self.journal, second['id'])
        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args[0]['session_id'], first['session_id'])
        closed = self.journal.round(first['id'])
        self.assertTrue(closed['cleanup_complete'])
        self.assertFalse(closed['session_open'])
        self.assertEqual(closed['finished_at'], 'original-finish')
        self.assertEqual(self.journal.findings()[0]['body'], 'Retained finding')

    def test_cleanup_failure_blocks_launch_and_requires_explicit_closure(self):
        first = self.orphan()
        second = self.prepared()
        with patch('agr.tmux.runtime.cleanup_container', side_effect=ReviewError('daemon unavailable')), patch('agr.tmux.run') as launch:
            with self.assertRaisesRegex(ReviewError, 'daemon unavailable'):
                launch_round(self.journal, second['id'])
            launch.assert_not_called()
        failed = self.journal.round(first['id'])
        self.assertFalse(failed['cleanup_complete'])
        self.assertIn('daemon unavailable', failed['session_error'])
        self.assertEqual(self.journal.round(second['id'])['status'], 'failed')
        third = self.prepared()
        with patch('agr.tmux.runtime.cleanup_container') as cleanup, patch('agr.tmux.run') as launch:
            with self.assertRaisesRegex(ReviewError, 'explicitly close'):
                launch_round(self.journal, third['id'])
            cleanup.assert_not_called()
            launch.assert_not_called()
        with patch('agr.tmux.runtime.cleanup_container') as cleanup:
            closed = tmux.close_session(self.journal, first['id'])
        cleanup.assert_called_once()
        self.assertTrue(closed['cleanup_complete'])
        self.assertIsNone(closed['session_error'])
        self.assertIn('daemon unavailable', closed['previous_session_error'])

    def test_live_worker_is_never_treated_as_an_orphan(self):
        first = self.orphan(worker_pid=os.getpid())
        with patch('agr.tmux.runtime.cleanup_container') as cleanup:
            with self.assertRaisesRegex(ReviewError, 'worker may still be running'):
                tmux.close_session(self.journal, first['id'])
            cleanup.assert_not_called()

    def test_recovery_cleans_docker_even_when_the_attached_client_survived(self):
        first = self.orphan(worker_pid=12345, reviewer_pid=os.getpid())
        self.journal.update_round(first['id'], status='running')
        with patch('agr.cli.tmux.alive', return_value=False), patch('agr.tmux.os.kill', side_effect=ProcessLookupError) as probe, patch('agr.tmux.runtime.cleanup_container') as cleanup:
            closed = recover(self.journal, first['id'])
        cleanup.assert_called_once()
        self.assertTrue(all(call.args == (12345, 0) for call in probe.call_args_list))
        self.assertEqual(closed['status'], 'interrupted')
        self.assertTrue(closed['cleanup_complete'])
        self.assertFalse(closed['session_open'])
        self.assertEqual(self.journal.findings()[0]['body'], 'Retained finding')

    def test_recovery_refuses_a_live_worker_before_changing_review_status(self):
        first = self.orphan(worker_pid=os.getpid())
        self.journal.update_round(first['id'], status='running')
        with patch('agr.cli.tmux.alive', return_value=False), patch('agr.tmux.runtime.cleanup_container') as cleanup:
            with self.assertRaisesRegex(ReviewError, 'worker may still be running'):
                recover(self.journal, first['id'])
            cleanup.assert_not_called()
        self.assertEqual(self.journal.round(first['id'])['status'], 'running')

    def test_stale_worker_pid_does_not_prevent_container_cleanup(self):
        first = self.orphan(worker_pid=12345)
        with patch('agr.tmux.os.kill', side_effect=ProcessLookupError), patch('agr.tmux.runtime.cleanup_container') as cleanup:
            closed = tmux.close_session(self.journal, first['id'])
        cleanup.assert_called_once()
        self.assertTrue(closed['cleanup_complete'])

    def test_foreign_pane_is_left_untouched_and_blocks_pending_cleanup(self):
        first = self.orphan()
        second = self.prepared()
        with patch('agr.tmux.pane_state', return_value='foreign'), patch('agr.tmux.runtime.cleanup_container') as cleanup, patch('agr.tmux.run') as terminal:
            with self.assertRaisesRegex(ReviewError, 'ownership changed'):
                tmux.cleanup_windows(self.journal, second['id'])
            cleanup.assert_not_called()
            terminal.assert_not_called()
        self.assertTrue(self.journal.round(first['id'])['session_open'])

    def test_old_confirmed_closure_does_not_use_current_docker_connection(self):
        first = self.orphan()
        del first['runtime']['docker_client']
        self.journal.update_round(first['id'], runtime=first['runtime'], session_open=False, closed_at='old-closure')
        second = self.prepared()
        with patch('agr.tmux.runtime.cleanup_container') as cleanup:
            tmux.cleanup_windows(self.journal, second['id'])
            tmux.close_session(self.journal, first['id'])
            cleanup.assert_not_called()
