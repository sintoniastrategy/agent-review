from unittest.mock import patch

from .support import RepositoryTest
from agr import ReviewError, names, progress, reporting
from agr.cli import execute, parser


class NamesTests(RepositoryTest):
    def test_padding_is_canonical_but_does_not_limit_counts(self):
        for number, slot, index in ((3, 1, 15), (100, 10, 1000)):
            prefix = names.reviewer(number, 'claude', slot)
            value = names.finding(prefix, index)
            self.assertEqual(names.parse_finding(value), (number, 'claude', slot, index))
        self.assertEqual(names.finding('r03-claude1', 15), 'r03-claude1-f015')
        for value in ('3', 'r3-claude1', 'r003-claude1', 'r03-claude01', 'r03-claude1-def', 'r00-claude1', '../r03-claude1'):
            with self.subTest(value=value), self.assertRaises(ReviewError):
                names.parse_reviewer(value)

    def test_cli_addresses_parallel_runs_by_pass_and_slot(self):
        first = self.prepared()
        with patch('agr.cli.runtime.setup', return_value=self.fake_runtime()):
            second = execute(parser().parse_args(['--repo', str(self.repo), 'prepare', '--parallel-with', 'r01-claude1']))
        self.assertEqual(second['directory'], 'r01-claude2')
        with patch('agr.cli.launch_round', return_value={}) as start:
            execute(parser().parse_args(['--repo', str(self.repo), 'start', 'r01-claude2']))
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0].directory, self.journal.directory)
        self.assertEqual(start.call_args.args[1:], (second['id'], 300))
        for record in (first, second):
            execute(parser().parse_args(['--repo', str(self.repo), 'cancel', record['directory']]))
        third = self.prepared()
        self.assertEqual((third['id'], third['directory']), (3, 'r02-claude1'))
        self.assertEqual(self.journal.select_round('r02-claude1'), 3)
        for value in ('3', 'r2-claude1', 'r03-claude1'):
            with self.subTest(value=value), self.assertRaises(ReviewError):
                execute(parser().parse_args(['--repo', str(self.repo), 'report', value]))

    def test_imports_continue_the_reviewer_finding_sequence(self):
        record = self.prepared()
        self.journal.update_round(record['id'], status='running')
        output = self.journal.round_directory(record['id']) / 'output'
        draft = output / 'drafts' / 'issue.md'
        draft.write_text('Published issue')
        published = reporting.publish(output, 'finding', draft)
        self.journal.update_round(record['id'], status='failed')
        imported = self.journal.add_finding(record['id'], {'body': 'Recovered issue'}, imported={'artifact': 'terminal.log'})
        self.assertEqual(published['id'], 'r01-claude1-f001')
        self.assertEqual(imported['id'], 'r01-claude1-f002')
        self.assertEqual(len(self.journal.findings()), 2)

    def test_human_queue_keeps_pass_and_disambiguates_reviewers(self):
        def item(identifier):
            return {'id': identifier, 'priority': 'P2', 'decision': 'pending', 'title': 'Issue'}
        text = progress.findings([item('r02-claude1-f019'), item('r03-claude1-f001')])
        self.assertIn('Reviewer: Claude 1', text)
        self.assertIn('R2-F19', text)
        self.assertIn('R3-F1', text)
        mixed = progress.findings([item('r03-claude1-f001'), item('r03-claude2-f001'), item('r03-codex1-f001')])
        self.assertIn('R3-Claude1-F1', mixed)
        self.assertIn('R3-Claude2-F1', mixed)
        self.assertIn('R3-Codex1-F1', mixed)

    def test_human_finding_shows_full_id_and_latest_recheck(self):
        number = self.running()
        finding = self.journal.add_finding(number, {'body': 'Evidence'})
        self.journal.update_round(number, status='completed')
        following = self.running()
        self.journal.reviewer_event(following, 'verification', finding=finding['id'], status='resolved', reason='Verified')
        text = execute(parser().parse_args(['--repo', str(self.repo), 'finding', finding['id'], '--human']))
        self.assertIn('R1-F1', text)
        self.assertIn('ID: r01-claude1-f001', text)
        self.assertIn('Recheck: resolved', text)
