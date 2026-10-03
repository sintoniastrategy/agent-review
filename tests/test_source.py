from .support import RepositoryTest
from agr.source import file_at, same_source, snapshot


class SourceTests(RepositoryTest):
    def test_snapshot_preserves_real_index_and_captures_working_files(self):
        (self.repo / "app.py").write_text("value = 2\n")
        self.git("add", "app.py")
        staged_before = self.git("diff", "--cached", "--binary")
        (self.repo / "app.py").write_text("value = 3\n")
        (self.repo / "new file.py").write_text("new_value = 1\n")
        state = snapshot(self.repo, "base")
        self.assertEqual(self.git("diff", "--cached", "--binary"), staged_before)
        self.assertEqual(file_at(self.repo, state["tree"], "app.py"), "value = 3\n")
        self.assertEqual(file_at(self.repo, state["tree"], "new file.py"), "new_value = 1\n")
        self.assertNotIn(b".agr", self.git("ls-tree", "-r", "--name-only", state["tree"]))
        self.assertTrue(same_source(state, snapshot(self.repo, "base")))

    def test_force_added_ignored_file_is_in_snapshot(self):
        (self.repo / ".gitignore").write_text("ignored.txt\n")
        (self.repo / "ignored.txt").write_text("staged on purpose\n")
        self.git("add", "-f", "ignored.txt")
        state = snapshot(self.repo, "base")
        self.assertEqual(file_at(self.repo, state["tree"], "ignored.txt"), "staged on purpose\n")

    def test_repeat_context_contains_all_decisions_and_correct_delta(self):
        (self.repo / "app.py").write_text("value = 2\n")
        first = self.prepared()
        self.journal.update_round(first["id"], status="running")
        finding = self.journal.add_finding(first["id"], {"body": "Old issue\nFull evidence", "severity": "P1"})
        self.journal.reviewer_event(first["id"], "report", body="Original full report")
        self.journal.update_round(first["id"], status="completed")
        self.journal.author_event("decision", finding=finding["id"], action="reject", reason="Pre-existing behavior outside the task")
        (self.repo / "app.py").write_text("value = 3\n")
        second = self.prepared()
        directory = self.journal.round_directory(second["id"])
        history = (directory / 'input' / 'history.md').read_text()
        self.assertIn(finding['body'], history)
        self.assertIn('Pre-existing behavior outside the task', history)
        self.assertIn('Original full report', history)
        delta = (directory / "input" / "since-previous.diff").read_text()
        full = (directory / "input" / "full.diff").read_text()
        self.assertIn("-value = 2", delta)
        self.assertIn("+value = 3", delta)
        self.assertIn("-value = 1", full)
        self.assertEqual(self.git("rev-parse", first["source"]["snapshot_ref"]).decode().strip(), first["source"]["tree"])
        prompt = (directory / "prompt.md").read_text()
        self.assertIn("Clean code principles", prompt)
        self.assertIn("Guidelines Compliance", prompt)
        self.assertIn("without any numerical limit", prompt)

    def test_source_changes_are_detected_even_without_head_change(self):
        first = snapshot(self.repo, "base")
        (self.repo / "app.py").write_text("value = 42\n")
        second = snapshot(self.repo, "base")
        self.assertEqual(first["head"], second["head"])
        self.assertFalse(same_source(first, second))

    def test_parallel_reviewers_share_frozen_prior_context(self):
        first = self.prepared()
        self.journal.update_round(first['id'], status='running')
        self.journal.add_finding(first['id'], {'body': 'New evidence from the active reviewer'})
        second = self.prepared(parallel_with=first['id'])
        first_input = self.journal.round_directory(first['id']) / 'input'
        second_input = self.journal.round_directory(second['id']) / 'input'
        self.assertEqual((first_input / 'history.md').read_text(), (second_input / 'history.md').read_text())
        self.assertNotIn('New evidence from the active reviewer', (second_input / 'history.md').read_text())
        self.assertEqual(first['source']['tree'], second['source']['tree'])
