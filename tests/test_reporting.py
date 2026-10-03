from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import multiprocessing
import os
from unittest.mock import patch

from .support import RepositoryTest
from agr import ReviewError
from agr.documents import atomic_text, read_document
from agr.reporting import complete, publish
from agr.store import Journal


def publish_series(output, owner):
    output = Path(output)
    for index in range(15):
        path = output / 'drafts' / ('%s-%02d.md' % (owner, index))
        path.write_text('Title: Concurrent finding\nSeverity: P1\n\nEvidence %s/%d' % (owner, index))
        publish(output, 'finding', path)


def record_decision(directory, identifier, number):
    journal = Journal(directory)
    return journal.author_event('decision', finding=identifier, action='defer', reason='Decision ' + str(number))


def add_series(directory, number):
    journal = Journal(directory)
    for index in range(15):
        journal.add_finding(number, {'body': 'Journal evidence %d' % index})


class ReportingTests(RepositoryTest):
    def output(self):
        record = self.prepared()
        self.journal.update_round(record['id'], status='running')
        return record, self.journal.round_directory(record['id']) / 'output'

    def test_multiprocess_publication_has_no_lost_or_partial_findings(self):
        record, output = self.output()
        with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context('spawn')) as pool:
            futures = [pool.submit(publish_series, output, str(index)) for index in range(3)]
            for future in futures:
                future.result(timeout=20)
        findings = self.journal.findings()
        self.assertEqual(len(findings), 45)
        self.assertEqual(len({item['id'] for item in findings}), 45)
        self.assertEqual(len({item['body'] for item in findings}), 45)
        self.assertEqual({item['id'] for item in findings}, {'r01-claude1-f%03d' % index for index in range(1, 46)})
        self.assertFalse(list(output.rglob('.draft-*')))

    def test_author_decisions_are_separate_append_only_files(self):
        number = self.running()
        finding = self.journal.add_finding(number, {'body': 'Original claim'})
        self.journal.update_round(number, status='completed')
        with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context('spawn')) as pool:
            futures = [pool.submit(record_decision, self.journal.directory, finding['id'], index) for index in range(12)]
            for future in futures:
                future.result(timeout=20)
        events = self.journal.rows('events')
        self.assertEqual(len(events), 12)
        self.assertEqual(len({event['sequence'] for event in events}), 12)
        self.assertEqual({event['reason'] for event in events}, {'Decision ' + str(index) for index in range(12)})
        self.assertEqual(len(list((self.journal.directory / 'decisions').glob('*.md'))), 12)
        self.assertEqual(self.journal.findings()[0]['body'], 'Original claim')

    def test_both_publication_paths_share_the_reviewer_lock_and_ids(self):
        record, output = self.output()
        with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context('spawn')) as pool:
            futures = [pool.submit(publish_series, output, 'reviewer'), pool.submit(add_series, self.journal.directory, record['id'])]
            for future in futures:
                future.result(timeout=20)
        identifiers = {item['id'] for item in self.journal.findings()}
        self.assertEqual(identifiers, {'r01-claude1-f%03d' % index for index in range(1, 31)})

    def test_draft_is_invisible_until_published_and_retry_is_idempotent(self):
        record, output = self.output()
        path = output / 'drafts' / 'issue.md'
        path.write_text('Severity: P1\n\nFirst line\nSecond line with "quotes" and backslashes \\')
        self.assertEqual(self.journal.findings(), [])
        first = publish(output, 'finding', path)
        self.assertEqual(publish(output, 'finding', path), first)
        self.assertEqual(len(self.journal.findings()), 1)
        self.assertEqual(self.journal.findings()[0]['body'], 'First line\nSecond line with "quotes" and backslashes \\')
        path.write_text('Severity: P1\n\nChanged')
        with self.assertRaisesRegex(ReviewError, 'changed'):
            publish(output, 'finding', path)

    def test_unpublished_drafts_remain_for_author_and_do_not_block_finish(self):
        record, output = self.output()
        bad = output / 'drafts' / 'issue.md'
        bad.write_text('Severity: invalid\n\nRetain this evidence')
        with self.assertRaises(ReviewError):
            publish(output, 'finding', bad)
        report = output / 'drafts' / 'summary.md'
        report.write_text('Coverage and limitations')
        publish(output, 'report', report)
        publish(output, 'finish')
        self.assertTrue(bad.exists())
        self.assertTrue((output / 'complete.json').exists())

    def test_completion_records_finish_without_claiming_document_integrity(self):
        record, output = self.output()
        with self.assertRaisesRegex(ReviewError, 'report'):
            publish(output, 'finish')
        report = output / 'drafts' / 'summary.md'
        report.write_text('Scope checked; no findings')
        publish(output, 'report', report)
        result = publish(output, 'finish')
        self.assertEqual(complete(output), result)
        self.assertEqual(publish(output, 'finish'), result)
        with (output / 'report.md').open('a') as stream:
            stream.write(' changed')
        self.assertEqual(complete(output), result)

    def test_rechecks_preserve_human_decisions(self):
        number = self.running()
        finding = self.journal.add_finding(number, {'body': 'Old claim'})
        self.journal.update_round(number, status='completed')
        self.journal.author_event('decision', finding=finding['id'], action='reject', reason='Outside scope')
        record, output = self.output()
        path = output / 'drafts' / 'check.md'
        path.write_text('Finding: ' + finding['id'] + '\nStatus: still_present\n\nConfirmed in the new snapshot')
        publish(output, 'check', path)
        item = self.journal.findings()[0]
        self.assertEqual(item['decision'], 'reject')
        self.assertEqual(item['verification']['status'], 'still_present')

    def test_atomic_publication_never_overwrites_and_failed_publish_retains_draft(self):
        destination = self.repo / '.agr' / 'record.md'
        atomic_text(destination, 'original')
        with self.assertRaises(FileExistsError):
            atomic_text(destination, 'replacement')
        self.assertEqual(destination.read_text(), 'original')
        record, output = self.output()
        path = output / 'drafts' / 'issue.md'
        path.write_text('Severity: P1\n\nEvidence')
        with patch('agr.documents.os.link', side_effect=OSError('simulated interrupted publication')):
            with self.assertRaises(OSError):
                publish(output, 'finding', path)
        self.assertEqual(self.journal.findings(), [])
        self.assertTrue(path.exists())
        self.assertFalse(list(output.rglob('.draft-*')))

    def test_plain_markdown_finding_needs_no_metadata_syntax(self):
        record, output = self.output()
        path = output / 'drafts' / 'plain.md'
        path.write_text('A concrete defect\n\nEvidence and a suggested fix.')
        finding = publish(output, 'finding', path)
        self.assertEqual(finding['severity'], 'PZ')
        self.assertEqual(finding['body'], path.read_text())
        self.assertTrue((output / 'findings' / (finding['id'] + '--pZ--plain.md')).is_file())

    def test_priority_and_opaque_draft_name_reach_filename_without_new_fields(self):
        record, output = self.output()
        path = output / 'drafts' / 'regression-timeout-style-timeout.md'
        path.write_text('Severity: P4\n\nA small consistency improvement')
        finding = publish(output, 'finding', path)
        published = output / 'findings' / 'r01-claude1-f001--p4--regression-timeout-style-timeout.md'
        self.assertEqual(read_document(published)['id'], finding['id'])
        self.assertEqual(finding['draft'], path.name)
        self.assertNotIn('Lens:', published.read_text())
        self.assertNotIn('Subagent:', published.read_text())
        self.assertEqual(publish(output, 'finding', path), finding)
        original = published.read_bytes()
        self.journal.update_round(record['id'], status='completed')
        self.journal.author_event('assessment', finding=finding['id'], priority='P1', reason='Larger impact', proposal='Fix')
        item = self.journal.findings()[0]
        self.assertEqual((item['severity'], item['priority']), ('P4', 'P1'))
        self.assertEqual(published.read_bytes(), original)
        self.assertEqual([file.name for file in published.parent.iterdir()], [published.name])

    def test_legacy_files_share_sequence_and_keep_references_with_new_files(self):
        record, output = self.output()
        legacy = output / 'findings' / 'r01-claude1-f007.md'
        legacy.parent.mkdir()
        legacy.write_text('Severity: unclassified\nDraft: old.md\n\nLegacy finding')
        old_draft = output / 'drafts' / 'old.md'
        old_draft.write_text('Legacy finding')
        old = publish(output, 'finding', old_draft)
        self.assertEqual(old['id'], legacy.stem)
        imported = output.parent / 'imports' / 'r01-claude1-f008.md'
        imported.parent.mkdir()
        imported.write_text('Severity: info\n\nImported legacy finding')
        source = output / 'drafts' / 'general-next.md'
        source.write_text('Severity: P4\n\nNext finding')
        latest = publish(output, 'finding', source)
        self.assertEqual(latest['id'], 'r01-claude1-f009')
        items = {item['id']: item for item in self.journal.findings()}
        self.assertEqual(items[legacy.stem]['priority'], 'PZ')
        self.assertEqual(items[imported.stem]['priority'], 'P4')
        self.journal.update_round(record['id'], status='completed')
        self.journal.author_event('decision', finding=old['id'], action='defer', reason='Later')
        following = self.running()
        next_output = self.journal.round_directory(following) / 'output'
        related = next_output / 'drafts' / 'related.md'
        related.write_text('Related-To: ' + old['id'] + '\nSeverity: P2\n\nNew evidence')
        self.assertEqual(publish(next_output, 'finding', related)['related_to'], old['id'])
        check = next_output / 'drafts' / 'recheck.md'
        check.write_text('Finding: ' + old['id'] + '\nStatus: still_present\n\nStill present')
        publish(next_output, 'check', check)
        saved = next(item for item in self.journal.findings() if item['id'] == old['id'])
        self.assertEqual(saved['decision'], 'defer')
        self.assertEqual(saved['verification']['status'], 'still_present')
        self.assertTrue(legacy.is_file())

    def test_long_draft_name_is_rejected_without_publishing_or_losing_the_draft(self):
        record, output = self.output()
        source = output / 'drafts' / ('x' * 240 + '.md')
        source.write_text('Evidence')
        with self.assertRaisesRegex(ReviewError, 'choose a shorter draft name'):
            publish(output, 'finding', source)
        self.assertTrue(source.is_file())
        self.assertEqual(self.journal.findings(), [])
