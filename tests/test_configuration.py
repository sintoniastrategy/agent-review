from pathlib import Path
from unittest.mock import patch

from .support import RepositoryTest
from agr import ReviewError, configuration, progress, read_json, write_json
from agr.cli import execute, parser
from agr.runner import claude_command


class ConfigurationTests(RepositoryTest):
    def command(self, *args):
        return execute(parser().parse_args(['--repo', str(self.repo), *args]))

    def preset(self, path, review='Custom review policy\n', workflow='Custom workflow\n'):
        path.mkdir(parents=True, exist_ok=True)
        for key, relative in configuration.PROMPT_FILES.items():
            target = path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(review if key == 'review' else workflow, encoding='utf-8')
        return path

    def skill(self):
        path = Path(self.temporary.name) / 'skill'
        self.preset(path / 'presets' / 'focused')
        (path / 'defaults.ini').write_text('[review]\nagent = claude\nmodel = sonnet\neffort = high\npreset = focused\nscope = full\n')
        protocol = path / 'prompts/reviewer/protocol.md'
        protocol.parent.mkdir(parents=True)
        protocol.write_text((configuration.SKILL / 'prompts/reviewer/protocol.md').read_text())
        return path

    def test_bundled_defaults_reach_frozen_round_and_launch_arguments(self):
        record = self.prepared()
        self.assertEqual((record['reviewer'], record['model'], record['effort'], record['preset']), ('claude', 'opus', 'xhigh', 'default'))
        directory = self.journal.round_directory(record['id'])
        args = claude_command(record, directory / 'output')
        self.assertEqual(args[args.index('--model') + 1], 'opus')
        self.assertEqual(args[args.index('--effort') + 1], 'xhigh')
        review = (directory / 'input/review.md').read_text()
        workflow = (directory / 'input/policy.md').read_text()
        self.assertIn('Clean code principles', review)
        self.assertIn('explain pre-existing behavior and scope concerns', review)
        self.assertNotIn('do not delegate to subagents', review)
        self.assertIn('do not delegate to subagents', workflow)
        self.assertIn('publication command with finish', (directory / 'input/protocol.md').read_text())
        self.assertIn(review.rstrip() + '\n\n' + workflow.rstrip(), (directory / 'prompt.md').read_text())
        self.assertIn('Model: opus | effort: xhigh | preset: default', progress.describe(self.journal, record['id']))

    def test_configure_preserves_fields_and_does_not_pin_inherited_defaults(self):
        credentials = Path(self.temporary.name) / 'credentials.json'
        first = self.command('configure', '--no-docker', '--credentials-file', str(credentials), '--window-name', 'repo/branch', '--effort', 'low')
        config = Path(first['config'])
        original = config.read_bytes()
        self.assertEqual(self.command('configure')['effort'], 'low')
        self.assertEqual(config.read_bytes(), original)
        second = self.command('configure', '--model', 'sonnet')
        self.assertEqual(second['runtime'], 'native')
        self.assertEqual(second['credentials_file'], str(credentials))
        self.assertEqual(second['window_name'], 'repo/branch')
        self.assertEqual(second['effort'], 'low')
        self.assertNotIn('agent', read_json(config))
        self.assertNotIn('preset', read_json(config))
        with patch('agr.configuration.SKILL', self.skill()):
            self.assertEqual(self.command('configure')['preset'], 'focused')
            inherited = self.command('configure', '--reset', 'effort', '--reset', 'model')
        self.assertEqual(inherited['effort'], 'high')
        self.assertEqual(inherited['model'], 'sonnet')
        self.assertNotIn('model', read_json(config))
        self.assertNotIn('effort', read_json(config))

    def test_prepare_precedence_and_preset_replacement(self):
        custom = self.preset(self.repo / '.agr/custom', 'Only check data loss.\n', 'Delegate focused checks.\n')
        with patch('agr.configuration.SKILL', self.skill()):
            self.assertEqual(self.command('configure')['model'], 'sonnet')
            self.command('configure', '--model', 'opus', '--effort', 'low')
            managed = self.fake_runtime()
            with patch('agr.cli.runtime.setup', return_value=managed):
                record = self.command('prepare', '--agent', 'claude', '--model', 'claude-specific-version', '--preset', './.agr/custom')
        self.assertEqual(record['model'], 'claude-specific-version')
        self.assertEqual(record['effort'], 'low')
        self.assertEqual(record['preset'], str(custom))
        self.assertEqual(record['directory'], 'r01-claude1')
        self.assertEqual(self.command('configure')['model'], 'opus')
        self.assertEqual(self.command('configure')['preset'], 'default')
        prompt = (self.journal.round_directory(record['id']) / 'prompt.md').read_text()
        self.assertTrue(prompt.startswith('Only check data loss.\n\nDelegate focused checks.\n'))
        self.assertNotIn('do not delegate to subagents', prompt)
        self.assertNotIn('Clean code principles', prompt)
        self.assertIn('Publication command:', prompt)

    def test_prepared_texts_and_launch_settings_survive_config_and_preset_edits(self):
        custom = self.preset(self.repo / '.agr/custom')
        self.command('configure', '--preset', './.agr/custom', '--model', 'sonnet', '--effort', 'high')
        record = self.prepared()
        directory = self.journal.round_directory(record['id'])
        originals = {name: (directory / name).read_bytes() for name in ('prompt.md', 'input/review.md', 'input/policy.md')}
        self.preset(custom, 'Changed review\n', 'Changed workflow\n')
        self.command('configure', '--model', 'opus', '--effort', 'max', '--preset', 'default')
        frozen = self.journal.round(record['id'])
        self.assertEqual((frozen['model'], frozen['effort'], frozen['preset']), ('sonnet', 'high', str(custom)))
        for name, content in originals.items():
            self.assertEqual((directory / name).read_bytes(), content)
        args = claude_command(frozen, directory / 'output')
        self.assertEqual(args[args.index('--model') + 1], 'sonnet')
        self.assertEqual(args[args.index('--effort') + 1], 'high')
        self.journal.update_round(record['id'], status='interrupted')
        next_record = self.prepared()
        self.assertEqual((next_record['model'], next_record['effort'], next_record['preset']), ('opus', 'max', 'default'))

    def test_parallel_reviewers_can_use_different_presets_and_models(self):
        first = self.prepared()
        custom = self.preset(self.repo / '.agr/custom')
        second = self.prepared(parallel_with=first['id'], preset=str(custom), model='sonnet', effort='medium')
        self.assertEqual(second['directory'], 'r01-claude2')
        self.assertEqual(second['preset'], str(custom))
        self.assertEqual(second['model'], 'sonnet')
        self.assertEqual(first['source']['tree'], second['source']['tree'])
        for filename in ('history.md', 'full.diff'):
            self.assertEqual((self.journal.round_directory(first['id']) / 'input' / filename).read_bytes(), (self.journal.round_directory(second['id']) / 'input' / filename).read_bytes())

    def test_bad_selection_fails_before_runtime_setup_or_round_creation(self):
        incomplete = self.repo / '.agr/incomplete'
        incomplete.mkdir()
        (incomplete / 'reviewer').mkdir()
        (incomplete / 'reviewer/review.md').write_text('Policy')
        cases = (
            ({'preset': 'missing'}, 'Cannot read preset file'),
            ({'preset': str(incomplete)}, 'policy.md'),
            ({'model': ''}, 'model must be'),
            ({'effort': 'extreme'}, 'Effort must be'),
            ({'agent': 'codex'}, 'not implemented'),
        )
        for values, message in cases:
            with self.subTest(values=values):
                write_json(self.repo / '.agr/config.json', values)
                with patch('agr.cli.runtime.setup') as setup:
                    with self.assertRaisesRegex(ReviewError, message):
                        self.command('prepare')
                    setup.assert_not_called()
                self.assertEqual(self.journal.rows('rounds'), [])

    def test_invalid_configuration_does_not_overwrite_existing_values(self):
        config = self.repo / '.agr/config.json'
        self.command('configure', '--model', 'sonnet')
        original = config.read_bytes()
        for args in (('--preset', 'missing'), ('--model', ''), ('--model', 'opus', '--reset', 'model')):
            with self.subTest(args=args):
                with self.assertRaises(ReviewError):
                    self.command('configure', *args)
                self.assertEqual(config.read_bytes(), original)

    def test_defaults_are_strict_and_prompt_text_is_not_interpolated(self):
        skill = self.skill()
        (skill / 'presets/focused/reviewer/review.md').write_text('Review {literal} with 100% coverage\n')
        with patch('agr.configuration.SKILL', skill):
            self.assertEqual(configuration.load_review(self.repo)['review_prompt'], 'Review {literal} with 100% coverage\n')
            for text in ('[review]\nmodel=opus\n', '[review]\nmodel=opus\nmodel=sonnet\n'):
                (skill / 'defaults.ini').write_text(text)
                with self.assertRaises(ReviewError):
                    configuration.load_review(self.repo)

    def test_human_configuration_and_historical_status_do_not_invent_defaults(self):
        text = self.command('configure', '--human')
        self.assertIn('model: opus', text)
        self.assertIn('preset: default', text)
        self.assertIn('Worktree overrides: none', text)
        record = self.prepared()
        self.journal.update_round(record['id'], model=None, effort=None, preset=None)
        text = progress.describe(self.journal, record['id'])
        self.assertIn('Model: not recorded | effort: not recorded | preset: not recorded', text)
