import json
import subprocess
import sys
from unittest.mock import patch

from .support import RepositoryTest
from agr.prompts import ENTRY
from agr.cli import execute, parser
from agr import runtime


class CLITests(RepositoryTest):
    def test_queue_table_reads_saved_findings_without_changing_priority_order_in_json(self):
        first = self.running()
        low = self.journal.add_finding(first, {'body': 'Low issue', 'title': 'Saved low priority title', 'severity': 'P3'})
        high = self.journal.add_finding(first, {'body': 'Critical issue', 'severity': 'P0'})
        self.journal.update_round(first, status='completed')
        self.journal.author_event('decision', finding=low['id'], action='defer', reason='Later')
        second = self.running()
        self.journal.reviewer_event(second, 'verification', finding=low['id'], status='resolved', reason='Checked')
        before = self.journal.export()
        result = subprocess.run([sys.executable, str(ENTRY), '--repo', str(self.repo), 'queue', '--table'], capture_output=True, text=True, check=True)
        self.assertLess(result.stdout.index('R1-F1'), result.stdout.index('R1-F2'))
        self.assertIn('Saved low priority title', result.stdout)
        self.assertIn('Deferred', result.stdout)
        self.assertIn('Confirmed resolved', result.stdout)
        self.assertIn('r02', result.stdout)
        self.assertIn('Not rechecked', result.stdout)
        self.assertEqual(self.journal.export(), before)
        self.assertEqual([item['id'] for item in self.command('queue')], [high['id'], low['id']])

    def test_human_prepare_and_status_keep_json_mode_available(self):
        with patch('agr.cli.runtime.setup', return_value=self.fake_runtime()):
            text = execute(parser().parse_args(['--repo', str(self.repo), 'prepare', '--human']))
        self.assertIn('prepared', text)
        self.assertIn('findings: 0', text)
        result = subprocess.run([sys.executable, str(ENTRY), '--repo', str(self.repo), 'status', '--human'], capture_output=True, text=True, check=True)
        self.assertIn('Reviewer has not reported a stage yet', result.stdout)
        self.assertIn('Findings across all passes: 0', result.stdout)
        self.assertIn('Decisions and fixes:', result.stdout)
        self.assertIn('Latest recorded reviewer recheck', result.stdout)
        data = self.command('status')
        self.assertEqual(data['rounds'][0]['status'], 'prepared')
        self.assertEqual(data['finding_counts'], {})

    def test_watch_exits_with_failure_status_and_keeps_results(self):
        record = self.prepared()
        self.journal.update_round(record['id'], status='failed', error='Test reviewer failed')
        result = subprocess.run([sys.executable, str(ENTRY), '--repo', str(self.repo), 'watch', record['directory']], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Reason: Test reviewer failed', result.stdout)
        self.assertEqual(self.journal.round(record['id'])['status'], 'failed')

    def command(self, *args):
        result = subprocess.run([sys.executable, str(ENTRY), "--repo", str(self.repo), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def prepare_command(self):
        config = runtime.configuration(self.repo)
        managed = {**self.fake_runtime(), "mode": config["runtime"], "window_name": config["window_name"]}
        with patch("agr.cli.runtime.setup", return_value=managed):
            return execute(parser().parse_args(["--repo", str(self.repo), "prepare"]))

    def test_docker_is_the_default_and_configuration_is_local(self):
        self.write_settings({'window_name': 'repo/feature'})
        prepared = self.prepare_command()
        self.assertEqual(prepared["runtime"]["mode"], "docker")
        self.assertEqual(prepared["runtime"]["window_name"], "repo/feature")
        self.assertNotIn("launcher", prepared)
        self.assertEqual(prepared["status"], "prepared")
        self.assertNotIn(b".agr", self.git("status", "--porcelain"))
        self.assertEqual(self.command("cancel", "r01-claude1")["status"], "interrupted")

    def test_runtime_is_frozen_for_the_prepared_round_and_native_is_explicit(self):
        self.write_settings({'runtime': 'native'})
        prepared = self.prepare_command()
        self.assertEqual(prepared["runtime"]["mode"], "native")
        self.write_settings({'runtime': 'docker'})
        self.assertEqual(self.journal.round(prepared["id"])["runtime"]["mode"], "native")
        self.command("cancel", prepared['directory'])
        self.assertEqual(self.prepare_command()["runtime"]["mode"], "docker")

    def test_json_settings_require_explicit_migration_before_any_install(self):
        config = self.repo / ".agr" / "config.json"
        config.write_text(json.dumps({'runtime': 'native', 'model': 'sonnet'}))
        result = subprocess.run([sys.executable, str(ENTRY), "--repo", str(self.repo), "prepare"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(config), result.stderr)
        self.assertIn('config.ini', result.stderr)
        self.assertIn('[review]', result.stderr)
        self.assertEqual(len(self.journal.rows("rounds")), 0)

    def test_configure_is_no_longer_a_command(self):
        result = subprocess.run([sys.executable, str(ENTRY), '--help'], capture_output=True, text=True, check=True)
        self.assertNotIn('configure', result.stdout)
        result = subprocess.run([sys.executable, str(ENTRY), 'configure'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid choice', result.stderr)

    def test_next_and_exports_keep_human_decisions_explicit(self):
        number = self.running()
        low = self.journal.add_finding(number, {"body": "Low", "severity": "P3"})["id"]
        critical = self.journal.add_finding(number, {"body": "Critical", "severity": "P0"})["id"]
        self.journal.update_round(number, status="completed")
        self.assertEqual(self.command("next")["id"], critical)
        self.command("decide", critical, "--action", "fix", "--reason", "User selected it")
        self.assertEqual(self.command("next")["id"], low)
        exported = self.command("export", "--finding", critical)
        self.assertEqual(len(exported["findings"]), 1)
        self.assertEqual(exported["findings"][0]["reason"], "User selected it")
        self.assertEqual(len(self.command("queue")), 2)

    def test_one_session_per_worktree_and_explicit_id_is_checked(self):
        from agr.store import Journal
        from agr import ReviewError
        with self.assertRaisesRegex(ReviewError, 'already has'):
            Journal.create(self.repo, 'base', 'Another task')
        explicit = self.command('--session', self.journal.manifest['id'], 'status')
        self.assertEqual(explicit['session']['id'], self.journal.manifest['id'])
        with self.assertRaisesRegex(ReviewError, 'Unknown session'):
            Journal.resolve(self.repo, 'wrong-id')

    def test_raw_only_finding_can_be_transcribed_without_losing_provenance(self):
        record = self.prepared()
        directory = self.journal.round_directory(record["id"])
        (directory / "response.md").write_text("An issue only in the raw report")
        self.journal.update_round(record["id"], status="failed")
        request = self.repo / ".agr" / "import.md"
        request.write_text("Severity: P1\n\nAn issue only in the raw report")
        imported = self.command("import-finding", record['directory'], "--markdown-file", str(request), "--source-artifact", "response.md", "--reason", "Reviewer omitted publication")
        self.assertEqual(imported["imported_by_author"]["artifact"], str(directory / "response.md"))
        self.assertEqual(self.command("next")["decision"], "pending")
        self.assertEqual((directory / "response.md").read_text(), imported["body"])
