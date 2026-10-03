from pathlib import Path
from unittest.mock import patch

from .support import RepositoryTest
from agr import ReviewError, read_json
from agr.cli import execute, parser
from agr.documents import read_document
from agr.reporting import publish
from agr.source import diff


class FlowTests(RepositoryTest):
    def command(self, *args):
        return execute(parser().parse_args(['--repo', str(self.repo), *args]))

    def test_decision_changes_do_not_erase_completed_fixes_and_batch_diff_is_retained(self):
        number = self.running()
        ids = [self.journal.add_finding(number, {'body': 'Issue ' + str(index)})['id'] for index in range(6)]
        self.journal.update_round(number, status='completed')
        self.command('decide', *ids, '--action', 'fix', '--reason', 'Human chose this group')
        batch = self.command('batch-open', *ids)
        self.command('decide', ids[0], '--action', 'reject', '--reason', 'Changed the scope')
        (self.repo / 'app.py').write_text('value = 2\n')
        done = self.command('batch-done', '--finding', ids[1], '--summary', 'Adjusted value', '--validation', 'Fixture assertion')
        patch_text = (self.journal.directory / done['diff']).read_text()
        self.assertIn('-value = 1', patch_text)
        self.assertIn('+value = 2', patch_text)
        self.assertEqual(done['before']['tree'], batch['source']['tree'])
        self.assertEqual(self.git('rev-parse', done['source']['snapshot_ref']).decode().strip(), done['source']['tree'])
        self.command('decide', ids[1], '--action', 'defer', '--reason', 'Reconsider this later')
        items = {item['id']: item for item in self.journal.findings()}
        self.assertEqual(items[ids[0]]['decision'], 'reject')
        self.assertNotIn('fix', items[ids[0]])
        self.assertEqual(items[ids[1]]['decision'], 'defer')
        self.assertEqual(items[ids[1]]['fix']['findings'], [ids[1]])
        self.assertIsNone(self.journal.active_batch())
        next_round = self.prepared()
        copied = self.journal.round_directory(next_round['id']) / 'input' / done['diff']
        self.assertEqual(copied.read_text(), patch_text)
        history = (copied.parent.parent / 'history.md').read_text()
        self.assertIn('Changed the scope', history)
        self.assertIn('Reconsider this later', history)
        self.assertNotIn('_text:', history)

    def test_changes_scope_uses_last_completed_snapshot_and_keeps_failed_findings(self):
        (self.repo / 'app.py').write_text('value = 2\n')
        first = self.prepared()
        self.journal.update_round(first['id'], status='completed')
        (self.repo / 'app.py').write_text('value = 3\n')
        failed = self.running()
        finding = self.journal.add_finding(failed, {'body': 'Partial evidence retained'})
        self.journal.update_round(failed, status='failed')
        (self.repo / 'app.py').write_text('value = 4\n')
        record = self.prepared(scope='changes')
        inputs = self.journal.round_directory(record['id']) / 'input'
        delta = (inputs / 'since-previous.diff').read_text()
        self.assertIn('-value = 2', delta)
        self.assertIn('+value = 4', delta)
        self.assertEqual(record['previous_review'], first['id'])
        self.assertIn(finding['body'], (inputs / 'history.md').read_text())
        self.assertIn('r02-claude1: failed', (inputs / 'history.md').read_text())
        prompt = (inputs.parent / 'prompt.md').read_text()
        self.assertIn('Your reviewer ID: r03-claude1', prompt)
        self.assertIn('Review scope: changes', prompt)
        self.assertEqual((inputs / 'reviewer.txt').read_text(), 'r03-claude1\n')
        self.assertIn(str(inputs / 'since-previous.diff'), prompt)

    def test_batch_diff_preserves_non_utf8_source_bytes(self):
        number = self.running()
        finding = self.journal.add_finding(number, {'body': 'Encoding-sensitive source'})
        self.journal.update_round(number, status='completed')
        self.command('decide', finding['id'], '--action', 'fix', '--reason', 'Approved')
        source = self.repo / 'latin.txt'
        source.write_bytes(b'old: \xfe\n')
        batch = self.command('batch-open', finding['id'])
        source.write_bytes(b'new: \xff\n')
        done = self.command('batch-done', '--finding', finding['id'], '--summary', 'Updated source', '--validation', 'Byte comparison')
        self.assertEqual((self.journal.directory / done['diff']).read_bytes(), diff(self.repo, batch['source']['tree'], done['source']['tree']))

    def test_first_changes_review_fails_before_runtime_setup(self):
        with patch('agr.cli.runtime.setup') as setup:
            with self.assertRaisesRegex(ReviewError, 'previous completed'):
                self.command('prepare', '--scope', 'changes')
            setup.assert_not_called()
        self.assertEqual(self.journal.rows('rounds'), [])

    def test_direct_recheck_and_report_need_only_human_fields(self):
        old = self.running()
        finding = self.journal.add_finding(old, {'body': 'Original evidence'})
        self.journal.update_round(old, status='completed')
        number = self.running()
        output = self.journal.round_directory(number) / 'output'
        check = output / 'checks' / (finding['id'] + '.md')
        check.parent.mkdir()
        check.write_text('Status: resolved\n\nThe invalid path is now rejected.')
        self.assertEqual(publish(output, 'check', check)['finding'], finding['id'])
        (output / 'report.md').write_text('Checked the scoped changes; retained an unfinished note.')
        pending = output / 'drafts' / 'note.md'
        pending.write_text('Note: needs author context')
        publish(output, 'finish')
        self.journal.update_round(number, status='completed')
        item = self.journal.findings()[0]
        self.assertEqual(item['verification']['status'], 'resolved')
        self.assertEqual(item['decision'], 'pending')
        self.assertTrue(pending.is_file())
        self.assertEqual(len(list((output / 'checks').glob('*.md'))), 1)
        self.assertNotIn('_text', check.read_text())
        self.assertEqual(set(read_json(output / 'complete.json')), {'round', 'finished_at'})

    def test_note_and_url_bodies_are_not_interpreted_as_metadata(self):
        number = self.running()
        output = self.journal.round_directory(number) / 'output'
        for index, body in enumerate(('Note: interesting defect', 'https://example.invalid\n\nEvidence')):
            source = output / 'drafts' / ('issue%d.md' % index)
            source.write_text(body)
            value = publish(output, 'finding', source)
            path = output / 'findings' / (value['id'] + '.md')
            self.assertEqual(read_document(path)['body'], body)
            self.assertNotIn('_text', path.read_text())
            self.assertNotIn('draft_sha256', path.read_text())
            self.assertNotIn('round:', path.read_text())

    def test_author_recommendations_and_priority_filters_do_not_change_decisions(self):
        number = self.running()
        medium = self.journal.add_finding(number, {'body': 'Medium claim', 'severity': 'P2'})
        low = self.journal.add_finding(number, {'body': 'Low claim', 'severity': 'P3'})
        self.command('assess', medium['id'], '--priority', 'P2', '--reason', 'Supported case', '--proposal', 'Handle it', '--recommendation', 'fix')
        table = self.command('queue', '--priority', 'P2', '--table')
        self.assertIn('R1-F1', table)
        self.assertNotIn('R1-F2', table)
        self.assertIn('Supported case', table)
        self.assertIn('Not discussed', table)
        self.assertEqual({item['decision'] for item in self.journal.findings()}, {'pending'})
        before = self.journal.rows('events')
        with self.assertRaises(ReviewError):
            self.command('decide', low['id'], 'r01-claude1-f999', '--action', 'fix', '--reason', 'Group')
        self.assertEqual(self.journal.rows('events'), before)

    def test_preflight_checks_prerequisites_without_installing_or_launching(self):
        managed = self.fake_runtime()
        self.command('configure', '--no-docker', '--credentials-file', managed['credentials_file'])
        with patch('agr.cli.shutil.which', side_effect=lambda name: '/tools/' + name), patch('agr.cli.runtime.setup') as setup:
            result = self.command('preflight', '--base', 'base')
            self.assertEqual(result['scope'], 'full')
            self.assertEqual(result['tools'], {'git': '/tools/git', 'tmux': '/tools/tmux'})
            setup.assert_not_called()
        with patch('agr.cli.shutil.which', return_value=None):
            with self.assertRaisesRegex(ReviewError, 'Missing required tools'):
                self.command('preflight', '--base', 'base')

    def test_author_instructions_express_proportional_checks_and_optional_delegation(self):
        discussion = self.command('instructions', 'discuss')
        fixing = self.command('instructions', 'fix')
        self.assertIn('do not conduct a second comprehensive review', discussion)
        self.assertIn('Decisions can change at any time', discussion)
        self.assertIn('main author session by default', fixing)
        self.assertIn('only with explicit authorization', fixing)
        self.assertEqual(self.journal.rows('rounds'), [])
