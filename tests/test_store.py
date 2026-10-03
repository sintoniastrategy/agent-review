from concurrent.futures import ThreadPoolExecutor

from .support import RepositoryTest
from agr import ReviewError
from agr.store import Journal


class StoreTests(RepositoryTest):
    def test_large_concurrent_queue_and_restart_preserve_every_finding(self):
        number = self.running()
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda value: self.journal.add_finding(number, {"body": "Complete finding " + str(value), "severity": "P1"}), range(73)))
        restored = Journal(self.journal.directory)
        findings = restored.findings()
        self.assertEqual(len(findings), 73)
        self.assertEqual(len({item["id"] for item in findings}), 73)
        self.assertEqual({item["body"] for item in findings}, {"Complete finding " + str(value) for value in range(73)})
        self.assertTrue(all(item["decision"] == "pending" for item in findings))

    def test_decisions_fixes_and_rechecks_are_separate_durable_events(self):
        number = self.running()
        fixed = self.journal.add_finding(number, {"body": "First issue", "severity": "P0"})["id"]
        pending = self.journal.add_finding(number, {"body": "Second issue"})["id"]
        self.journal.update_round(number, status="completed")
        self.journal.author_event("assessment", finding=fixed, priority="P1", reason="Bounded impact", proposal="Validate input")
        self.journal.author_event("decision", finding=fixed, action="fix", reason="User approved validation")
        self.journal.open_batch([fixed])
        self.journal.close_batch("Validation added", "Targeted tests passed", {"head": "example"})
        second = self.running()
        self.journal.reviewer_event(second, "verification", finding=fixed, status="resolved", reason="Invalid input is rejected")
        data = Journal(self.journal.directory).export()
        by_id = {item["id"]: item for item in data["findings"]}
        self.assertEqual(by_id[fixed]["severity"], "P0")
        self.assertEqual(by_id[fixed]["priority"], "P1")
        self.assertEqual(by_id[fixed]["decision"], "fix")
        self.assertEqual(by_id[fixed]["verification"]["status"], "resolved")
        self.assertEqual(by_id[pending]["decision"], "pending")
        self.assertEqual(len([event for event in data["events"] if event["kind"] == "decision"]), 1)

    def test_large_batches_allow_changed_decisions_and_explicit_partial_completion(self):
        number = self.running()
        identifiers = [self.journal.add_finding(number, {"body": str(index)})["id"] for index in range(6)]
        self.journal.update_round(number, status="completed")
        with self.assertRaises(ReviewError):
            self.journal.open_batch(identifiers[:1])
        for identifier in identifiers:
            self.journal.author_event("decision", finding=identifier, action="fix", reason="Explicitly approved")
        self.journal.open_batch(identifiers)
        self.journal.author_event("decision", finding=identifiers[0], action="reject", reason="Changed my mind")
        self.journal.close_batch("Stopped halfway", "", {}, cancel=True)
        self.assertIsNone(self.journal.active_batch())
        by_id = {item["id"]: item for item in self.journal.findings()}
        self.assertEqual(by_id[identifiers[0]]["decision"], "reject")
        self.assertTrue(all(by_id[value]["decision"] == "fix" for value in identifiers[1:]))

    def test_stop_keeps_undiscussed_findings_and_requires_reopen(self):
        number = self.running()
        self.journal.add_finding(number, {"body": "Still pending"})
        self.journal.update_round(number, status="interrupted")
        self.journal.set_state("stopped", "Enough for this task")
        with self.assertRaises(ReviewError):
            self.prepared()
        self.assertEqual(self.journal.findings()[0]["decision"], "pending")
        self.journal.set_state("active", "User requested another round")
        self.assertEqual(self.prepared()["id"], 2)
