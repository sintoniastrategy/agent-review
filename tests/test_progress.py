import os
from unittest.mock import patch

from .support import RepositoryTest
from agr import now, progress, reporting


class ProgressTests(RepositoryTest):
    def test_finding_table_wraps_full_titles_and_uses_the_recheck_pass(self):
        title = 'Stored reviewer title ' * 30 + 'END_OF_TITLE'
        item = {
            'id': 'r02-claude1-f019', 'priority': 'P3', 'title': title,
            'decision': 'pending', 'verification': {'round': 8, 'status': 'changed'},
        }
        text = progress.finding_table([item], [{'id': 8, 'pass': 3}], width=140)
        lines = [line for line in text.splitlines() if line.startswith('|')]
        header = [cell.strip() for cell in lines[0].split('|')[1:-1]]
        self.assertEqual(header, ['Finding', 'Priority', 'Title', 'Author proposes', 'Reason', 'Decision', 'Fix recorded', 'Reviewer verdict', 'Check pass'])
        self.assertIn('Reviewer: Claude 1', text)
        self.assertIn('R2-F19', lines[1])
        self.assertIn('Not discussed', lines[1])
        self.assertIn('Changed', lines[1])
        self.assertIn('r03', lines[1])
        rendered = ' '.join(line.split('|')[3].strip() for line in lines[1:])
        self.assertEqual(' '.join(rendered.split()), title)
        self.assertEqual(item['title'], title)
        self.assertTrue(all(len(line) <= 140 for line in text.splitlines()))

    def test_finding_table_orders_passes_slots_and_finding_numbers_numerically(self):
        identifiers = ['r100-claude1-f001', 'r03-claude10-f001', 'r03-claude2-f012', 'r03-codex1-f001', 'r03-claude2-f002', 'r99-claude1-f001']
        items = [{'id': value, 'priority': 'P2', 'decision': 'fix', 'title': value} for value in identifiers]
        text = progress.finding_table(items, [], width=200)
        rows = [[cell.strip() for cell in line.split('|')[1:-1]] for line in text.splitlines() if line.startswith('| R')]
        self.assertEqual([row[0] for row in rows], [
            'R3-Claude2-F2', 'R3-Claude2-F12', 'R3-Claude10-F1',
            'R3-Codex1-F1', 'R99-Claude1-F1', 'R100-Claude1-F1',
        ])
        self.assertNotIn('Reviewer:', text)
        self.assertTrue(all(row[-2:] == ['Not rechecked', '-'] for row in rows))
        self.assertTrue(all(row[-4] == 'Approved to fix' for row in rows))

    def test_finding_table_handles_empty_queue(self):
        self.assertEqual(progress.finding_table([], []), 'No findings.')

    def test_status_has_zero_counts_before_any_review(self):
        text = progress.status(self.journal)
        self.assertIn('Findings across all passes: 0', text)
        self.assertIn('Not discussed: 0', text)
        self.assertIn('Confirmed resolved: 0', text)
        self.assertIn('No review rounds prepared.', text)

    def test_status_separates_decisions_from_latest_rechecks_and_uses_pass_numbers(self):
        first = self.running()
        findings = [self.journal.add_finding(first, {'body': 'Issue %d' % index})['id'] for index in range(7)]
        self.journal.update_round(first, status='completed')
        for index, action in ((0, 'fix'), (1, 'fix'), (3, 'defer'), (4, 'reject')):
            self.journal.author_event('decision', finding=findings[index], action=action, reason='Human decision')
        self.journal.open_batch([findings[0]])
        self.journal.close_batch('Implemented', 'Tests passed', {})
        second = self.prepared()
        peer = self.prepared(parallel_with=second['id'])
        for record in (second, peer):
            self.journal.update_round(record['id'], status='running')
        for index, state in ((0, 'resolved'), (1, 'resolved'), (2, 'changed'), (3, 'uncertain'), (6, 'still_present')):
            self.journal.reviewer_event(second['id'], 'verification', finding=findings[index], status=state, reason='First reviewer')
        self.journal.reviewer_event(peer['id'], 'verification', finding=findings[0], status='still_present', reason='Later evidence')
        intermediate = progress.status(self.journal)
        self.assertIn('Confirmed resolved: 1 (r02: 1)', intermediate)
        self.assertIn('Still present: 2 (r02: 2)', intermediate)
        for record in (second, peer):
            self.journal.update_round(record['id'], status='completed')
        third = self.running()
        self.assertEqual(third, 4)
        for index in (0, 4):
            self.journal.reviewer_event(third, 'verification', finding=findings[index], status='resolved', reason='Latest check')
        text = progress.status(self.journal)
        for line in (
            'Findings across all passes: 7', 'Not discussed: 3', 'Approved to fix: 2',
            'Fix recorded (independent of current decision): 1', 'Deferred: 1', 'Rejected: 1', 'Not rechecked: 1',
            'Confirmed resolved: 3 (r02: 1, r03: 2)', 'Still present: 1 (r02: 1)',
            'Changed: 1 (r02: 1)', 'Uncertain: 1 (r02: 1)', 'r03-claude1 | running',
        ):
            self.assertIn(line, text)
        self.assertNotIn('r04:', text)

    def running_round(self):
        record = self.prepared()
        self.journal.update_round(record['id'], status='running', prompt_sent_at=now())
        return record['id'], self.journal.round_directory(record['id']) / 'output'

    def test_reviewer_message_does_not_change_canonical_completion(self):
        number, output = self.running_round()
        (output / 'progress.txt').write_text('Completed everything')
        text = progress.describe(self.journal, number)
        self.assertIn('running (reviewing)', text)
        self.assertIn('report: pending', text)
        self.assertIn('Reviewer last update', text)
        self.assertIn('Completed everything', text)
        self.assertEqual(self.journal.round(number)['status'], 'running')

    def test_watch_reports_publications_and_stops_on_completion(self):
        number, output = self.running_round()
        messages = []
        def publish_finding():
            (output / 'progress.txt').write_text('Checking runtime startup')
            draft = output / 'drafts' / 'issue.md'
            draft.write_text('Title: Runtime issue\nSeverity: P2\n\nEvidence')
            reporting.publish(output, 'finding', draft)
        def finish():
            draft = output / 'drafts' / 'summary.md'
            draft.write_text('Coverage and limitations')
            reporting.publish(output, 'report', draft)
            reporting.publish(output, 'finish')
            self.journal.update_round(number, status='completed', session_open=True)
        steps = iter((publish_finding, finish))
        with patch('agr.progress.time.sleep', side_effect=lambda _: next(steps)()):
            status = progress.watch(self.journal, number, emit=messages.append)
        self.assertEqual(status, 'completed')
        self.assertEqual(len(messages), 3)
        self.assertIn('Checking runtime startup', messages[1])
        self.assertIn('findings: 1', messages[1])
        self.assertIn('completed (Claude session open)', messages[2])
        self.assertIn('report: saved', messages[2])
        self.assertTrue(self.journal.round(number)['session_open'])

    def test_watch_reports_quiet_period_without_guessing_work(self):
        number, output = self.running_round()
        messages = []
        clock = [0]
        def tick(_):
            clock[0] += 60
            if clock[0] == 120:
                self.journal.update_round(number, status='stalled', error='No progress')
        with patch('agr.progress.time.monotonic', side_effect=lambda: clock[0]), patch('agr.progress.time.sleep', side_effect=tick):
            status = progress.watch(self.journal, number, emit=messages.append)
        self.assertEqual(status, 'stalled')
        self.assertIn('No new stage or publication for 60s', messages[1])
        self.assertIn('Terminal output: not yet', messages[1])
        self.assertIn('Reason: No progress', messages[-1])

    def test_interrupting_watch_does_not_cancel_review(self):
        number, output = self.running_round()
        before = self.journal.round(number)
        messages = []
        with patch('agr.progress.time.sleep', side_effect=KeyboardInterrupt):
            self.assertIsNone(progress.watch(self.journal, number, emit=messages.append))
        self.assertEqual(self.journal.round(number), before)
        self.assertIn('Stopped watching', messages[-1])

    def test_progress_reader_rejects_symlinks_and_nonregular_files(self):
        number, output = self.running_round()
        outside = self.repo / 'private.txt'
        outside.write_text('private value')
        path = output / 'progress.txt'
        path.symlink_to(outside)
        self.assertNotIn('private value', progress.describe(self.journal, number))
        path.unlink()
        os.mkfifo(path)
        self.assertEqual(progress.reviewer_update(path), ('Progress file is not a regular file', None))
