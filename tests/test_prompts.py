from .support import RepositoryTest
from agr import configuration
from agr.cli import execute, parser


class PromptTests(RepositoryTest):
    def custom_preset(self):
        directory = self.repo / '.agr/custom-perspectives'
        review = directory / 'reviewer/review.md'
        review.parent.mkdir(parents=True)
        review.write_text('Delegate independent checks of data compatibility and resource ownership.\n')
        for relative in configuration.PROMPT_FILES.values():
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Obsolete custom instruction: ' + relative + '\n')
        return directory

    def test_both_agents_freeze_the_same_common_rules_for_each_preset(self):
        custom = self.custom_preset()
        common = {part: configuration.skill_prompt(part) for part in configuration.PROMPT_FILES}
        frozen = {}
        for agent in ('claude', 'codex'):
            for preset in ('default', 'lenses', str(custom)):
                with self.subTest(agent=agent, preset=preset):
                    record = self.prepared(agent=agent, preset=preset)
                    directory = self.journal.round_directory(record['id'])
                    inputs = directory / 'input'
                    prompt = (directory / 'prompt.md').read_text()
                    selected = configuration.load_review(self.repo, {'agent': agent, 'preset': preset})
                    parts = [selected['review_prompt'], common['policy'], common['protocol']]
                    for part in ('review', 'policy', 'protocol'):
                        self.assertEqual((inputs / (part + '.md')).read_text(), selected[part + '_prompt'])
                    if record['previous_review'] is not None:
                        self.assertEqual((inputs / 'followup.md').read_text(), common['followup'])
                        parts.append(common['followup'])
                    else:
                        self.assertFalse((inputs / 'followup.md').exists())
                    prefix = '\n\n'.join(part.rstrip() for part in parts)
                    self.assertTrue(prompt.startswith(prefix + '\n\nYour reviewer ID: ' + record['directory'] + '\n'))
                    self.assertIn('Source directory: ' + str(self.repo), prompt)
                    self.assertIn('Snapshot tree: ' + record['source']['tree'], prompt)
                    self.assertNotIn('Obsolete custom instruction:', prompt)
                    for part in ('discuss', 'fix'):
                        self.assertFalse((inputs / (part + '.md')).exists())
                        self.assertNotIn(common[part].rstrip(), prompt)
                    frozen[directory / 'prompt.md'] = prompt
                    self.journal.update_round(record['id'], status='completed')
        (custom / 'reviewer/review.md').write_text('Changed criteria for future reviews.\n')
        self.write_settings({'agent': 'codex', 'preset': str(custom)})
        for path, content in frozen.items():
            self.assertEqual(path.read_text(), content)
        for phase in ('discuss', 'fix'):
            self.assertEqual(execute(parser().parse_args(['--repo', str(self.repo), 'instructions', phase])), common[phase])

    def test_custom_delegation_inherits_discovery_publication_and_completion_rules(self):
        custom = self.custom_preset()
        for agent in ('claude', 'codex'):
            with self.subTest(agent=agent):
                selected = configuration.load_review(self.repo, {'agent': agent, 'preset': str(custom)})
                self.assertNotIn('AGENTS.md', selected['review_prompt'])
                self.assertIn('Locate and read applicable AGENTS.md and CLAUDE.md', selected['policy_prompt'])
                self.assertIn('full common policy, publication protocol and any follow-up instructions', selected['policy_prompt'])
                self.assertIn('The main reviewer checks each candidate', selected['policy_prompt'])
                self.assertIn('For a merged finding, use any one source draft name', selected['protocol_prompt'])
                self.assertIn('account for every draft', selected['protocol_prompt'])
                self.assertIn('All launched subagents must have returned or stopped', selected['protocol_prompt'])
                self.assertNotIn('lenses', selected['policy_prompt'] + selected['protocol_prompt'])

    def test_parallel_changes_reviews_share_followup_rules_for_custom_delegation(self):
        custom = self.custom_preset()
        previous = self.prepared()
        self.journal.update_round(previous['id'], status='completed')
        (self.repo / 'app.py').write_text('value = 2\n')
        claude = self.prepared(agent='claude', preset=str(custom), scope='changes')
        codex = self.prepared(agent='codex', preset=str(custom), scope='changes', parallel_with=claude['id'])
        followup = configuration.skill_prompt('followup')
        for record in (claude, codex):
            with self.subTest(agent=record['reviewer']):
                self.assertEqual(record['previous_review'], previous['id'])
                directory = self.journal.round_directory(record['id'])
                self.assertEqual((directory / 'input/followup.md').read_text(), followup)
                self.assertIn(followup.rstrip(), (directory / 'prompt.md').read_text())
                self.assertIn('Primary diff: ' + str(directory / 'input/since-previous.diff'), (directory / 'prompt.md').read_text())
                self.assertIn('-value = 1', (directory / 'input/since-previous.diff').read_text())
                self.assertIn('+value = 2', (directory / 'input/since-previous.diff').read_text())
        self.assertIn('subagents return their evidence and conclusions', followup)
        self.assertEqual(claude['source']['tree'], codex['source']['tree'])
