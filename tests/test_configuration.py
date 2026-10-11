from pathlib import Path
import shutil
from unittest.mock import patch

from .support import RepositoryTest
from agr import ReviewError, configuration, progress, runtime
from agr.cli import execute, parser
from agr.runner import claude_command


class ConfigurationTests(RepositoryTest):
    def command(self, *args):
        return execute(parser().parse_args(['--repo', str(self.repo), *args]))

    def preset(self, path, review='Custom review criteria\n'):
        target = path / 'reviewer/review.md'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(review, encoding='utf-8')
        return path

    def skill(self):
        path = Path(self.temporary.name) / 'skill'
        self.preset(path / 'presets' / 'focused')
        defaults = (configuration.SKILL / 'defaults.ini').read_text()
        (path / 'defaults.ini').write_text(defaults.replace('preset = default', 'preset = focused').replace('model = opus', 'model = sonnet').replace('effort = xhigh', 'effort = high', 1))
        shutil.copytree(configuration.SKILL / 'prompts', path / 'prompts')
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
        self.assertIn('do not delegate to subagents', review)
        self.assertNotIn('do not delegate to subagents', workflow)
        self.assertIn('publication command with finish', (directory / 'input/protocol.md').read_text())
        self.assertIn(review.rstrip() + '\n\n' + workflow.rstrip(), (directory / 'prompt.md').read_text())
        self.assertIn('Model: opus | effort: xhigh | preset: default', progress.describe(self.journal, record['id']))

    def test_manual_settings_are_read_without_rewriting_or_pinning_defaults(self):
        credentials = Path(self.temporary.name) / 'credentials.json'
        config = self.write_settings({'runtime': 'native', 'credentials_file': credentials, 'window_name': 'repo/branch', 'effort': 'low'})
        original = config.read_bytes()
        settings = runtime.configuration(self.repo)
        self.assertEqual(settings['runtime'], 'native')
        self.assertEqual(settings['credentials_file'], str(credentials))
        self.assertEqual(settings['window_name'], 'repo/branch')
        self.assertEqual(configuration.load_review(self.repo)['effort'], 'low')
        self.assertEqual(config.read_bytes(), original)
        with patch('agr.configuration.SKILL', self.skill()):
            self.assertEqual(configuration.load_review(self.repo)['preset'], 'focused')
            self.write_settings({'runtime': 'native'})
            inherited = configuration.load_review(self.repo)
        self.assertEqual(inherited['effort'], 'high')
        self.assertEqual(inherited['model'], 'sonnet')
        self.assertEqual(configuration.local(self.repo), {'runtime': 'native'})

    def test_prepare_precedence_and_preset_replacement(self):
        custom = self.preset(self.repo / '.agr/custom', 'Only check data loss.\nDelegate focused checks.\n')
        with patch('agr.configuration.SKILL', self.skill()):
            self.assertEqual(configuration.load_review(self.repo)['model'], 'sonnet')
            self.write_settings({'model': 'opus', 'effort': 'low'})
            managed = self.fake_runtime()
            with patch('agr.cli.runtime.setup', return_value=managed):
                record = self.command('prepare', '--agent', 'claude', '--model', 'claude-specific-version', '--preset', './.agr/custom')
        self.assertEqual(record['model'], 'claude-specific-version')
        self.assertEqual(record['effort'], 'low')
        self.assertEqual(record['preset'], str(custom))
        self.assertEqual(record['directory'], 'r01-claude1')
        self.assertEqual(configuration.load_review(self.repo)['model'], 'opus')
        self.assertEqual(configuration.load_review(self.repo)['preset'], 'default')
        prompt = (self.journal.round_directory(record['id']) / 'prompt.md').read_text()
        self.assertTrue(prompt.startswith('Only check data loss.\nDelegate focused checks.\n'))
        self.assertNotIn('do not delegate to subagents', prompt)
        self.assertNotIn('Clean code principles', prompt)
        self.assertIn('Publication command:', prompt)

    def test_prepared_texts_and_launch_settings_survive_config_and_preset_edits(self):
        custom = self.preset(self.repo / '.agr/custom')
        self.write_settings({'preset': './.agr/custom', 'model': 'sonnet', 'effort': 'high'})
        record = self.prepared()
        directory = self.journal.round_directory(record['id'])
        originals = {name: (directory / name).read_bytes() for name in ('prompt.md', 'input/review.md', 'input/policy.md')}
        self.preset(custom, 'Changed review\n')
        self.write_settings({'model': 'opus', 'effort': 'max', 'preset': 'default'})
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
        (incomplete / 'reviewer/review.md').write_text(' \n')
        cases = (
            ({'preset': 'missing'}, 'Cannot read preset file'),
            ({'preset': str(incomplete)}, 'must not be empty'),
            ({'model': ''}, 'model must be'),
            ({'effort': 'extreme'}, 'Effort must be'),
            ({'agent': 'unknown'}, 'Agent must be'),
            ({'runtime': 'podman'}, 'Runtime must be'),
            ({'auth': 'unknown'}, 'Claude auth must be'),
        )
        for values, message in cases:
            with self.subTest(values=values):
                self.write_settings(values)
                with patch('agr.cli.runtime.setup') as setup:
                    with self.assertRaisesRegex(ReviewError, message):
                        self.command('prepare')
                    setup.assert_not_called()
                self.assertEqual(self.journal.rows('rounds'), [])

    def test_custom_presets_cannot_replace_skill_instructions(self):
        custom = self.preset(self.repo / '.agr/custom', 'Check compatibility.\n')
        expected = configuration.load_review(self.repo)
        for relative in configuration.PROMPT_FILES.values():
            path = custom / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'\xff')
        self.write_settings({'preset': str(custom)})
        selected = configuration.load_review(self.repo)
        self.assertEqual(selected['review_prompt'], 'Check compatibility.\n')
        for part in configuration.PROMPT_FILES:
            self.assertEqual(selected[part + '_prompt'], expected[part + '_prompt'])
        for phase in ('discuss', 'fix'):
            self.assertEqual(self.command('instructions', phase), expected[phase + '_prompt'])
        self.write_settings({'preset': 'missing'})
        self.assertEqual(self.command('instructions', 'discuss'), expected['discuss_prompt'])

    def test_lenses_first_and_followup_prompts_share_frozen_skill_instructions(self):
        selected = configuration.load_review(self.repo, {'preset': 'lenses'})
        with patch('agr.cli.runtime.setup', return_value=self.fake_runtime()):
            first = self.command('prepare', '--preset', 'lenses')
        directory = self.journal.round_directory(first['id'])
        self.assertEqual(first['preset'], 'lenses')
        self.assertEqual((directory / 'input/review.md').read_text(), selected['review_prompt'])
        self.assertFalse((directory / 'input/followup.md').exists())
        first_prompt = (directory / 'prompt.md').read_text()
        self.assertIn('three independent subagents', first_prompt)
        self.assertNotIn('Work independently; do not delegate to subagents.', first_prompt)
        self.journal.update_round(first['id'], status='completed')
        second = self.prepared(preset='lenses', scope='changes')
        following = self.journal.round_directory(second['id'])
        self.assertEqual(second['previous_review'], first['id'])
        for part in ('review', 'policy', 'protocol', 'followup'):
            expected = selected[part + '_prompt']
            self.assertEqual((following / 'input' / (part + '.md')).read_text(), expected)
            self.assertIn(expected.rstrip(), (following / 'prompt.md').read_text())
        self.assertEqual((directory / 'prompt.md').read_text(), first_prompt)

    def test_missing_common_prompt_fails_before_runtime_setup(self):
        skill = self.skill()
        (skill / 'prompts/reviewer/policy.md').unlink()
        with patch('agr.configuration.SKILL', skill), patch('agr.cli.runtime.setup') as setup:
            with self.assertRaisesRegex(ReviewError, 'Cannot read skill prompt file:.*policy.md'):
                self.command('prepare')
            setup.assert_not_called()
        self.assertEqual(self.journal.rows('rounds'), [])

    def test_common_prompt_edits_apply_only_to_future_preparations(self):
        skill = self.skill()
        with patch('agr.configuration.SKILL', skill):
            first = self.prepared()
            directory = self.journal.round_directory(first['id'])
            saved = {name: (directory / name).read_bytes() for name in ('prompt.md', 'input/policy.md', 'input/protocol.md')}
            (skill / 'prompts/reviewer/policy.md').write_text('Updated common policy.\n')
            (skill / 'prompts/reviewer/protocol.md').write_text('Updated publication instructions.\n')
            self.journal.update_round(first['id'], status='interrupted')
            second = self.prepared()
            following = self.journal.round_directory(second['id'])
            self.assertIn('Updated common policy.', (following / 'prompt.md').read_text())
            self.assertIn('Updated publication instructions.', (following / 'prompt.md').read_text())
            for name, content in saved.items():
                self.assertEqual((directory / name).read_bytes(), content)

    def test_invalid_ini_reports_its_path_without_rewriting_or_installing(self):
        config = self.repo / '.agr/config.ini'
        cases = (
            '[review]\nmodel = opus\nmodel = sonnet\n',
            '[review]\nefort = high\n',
            '[review]\n[extra]\nmodel = sonnet\n',
            '[DEFAULT]\nmodel = opus\n[review]\n',
            'model = sonnet\n',
        )
        for text in cases:
            with self.subTest(text=text):
                config.write_text(text)
                with patch('agr.cli.runtime.setup') as setup:
                    with self.assertRaises(ReviewError) as error:
                        self.command('prepare')
                    setup.assert_not_called()
                self.assertIn(str(config), str(error.exception))
                self.assertEqual(config.read_text(), text)
                self.assertEqual(self.journal.rows('rounds'), [])

    def test_defaults_are_strict_and_prompt_text_is_not_interpolated(self):
        skill = self.skill()
        defaults = skill / 'defaults.ini'
        original = defaults.read_text()
        (skill / 'presets/focused/reviewer/review.md').write_text('Review {literal} with 100% coverage\n')
        with patch('agr.configuration.SKILL', skill):
            self.assertEqual(configuration.load_review(self.repo)['review_prompt'], 'Review {literal} with 100% coverage\n')
            cases = (
                '[review]\nmodel=opus\n',
                '[review]\nmodel=opus\nmodel=sonnet\n',
                original.replace('runtime = docker\n', ''),
                original.replace('model = sonnet\n', ''),
                original.replace('model = gpt-6.1-sol\n', ''),
                original.split('[codex]')[0],
                original.replace('agent = claude', 'agent = unknown'),
                original.replace('[review]', '[review]\nmodel = misplaced'),
                original.replace('[codex]', '[codex]\nunknown = value'),
            )
            for text in cases:
                with self.subTest(text=text), patch('agr.cli.runtime.setup') as setup:
                    defaults.write_text(text)
                    with self.assertRaises(ReviewError):
                        self.command('prepare')
                    setup.assert_not_called()
                    self.assertEqual(defaults.read_text(), text)
                    self.assertEqual(self.journal.rows('rounds'), [])
            defaults.unlink()
            with self.assertRaisesRegex(ReviewError, 'Cannot read configuration:.*defaults.ini'):
                configuration.load_review(self.repo)

    def test_shared_defaults_keep_common_values_and_each_agents_supported_options(self):
        skill = self.skill()
        path = skill / 'defaults.ini'
        values = configuration.read_ini(path)
        values.update(scope='changes', window_name='shared-window')
        values['claude'].update(auth='file', credentials_file='claude.json')
        values['codex'].update(effort='ultra', profile='codex-profile')
        self.write_settings(values, path)
        backend = configuration.agents.adapter('codex')
        with patch('agr.configuration.SKILL', skill), patch.object(backend, 'OPTIONS', (*backend.OPTIONS, 'profile')):
            claude = configuration.effective(self.repo)
            codex = configuration.effective(self.repo, overrides={'agent': 'codex'})
        for selected in (claude, codex):
            self.assertEqual((selected['preset'], selected['scope'], selected['runtime'], selected['window_name']), ('focused', 'changes', 'docker', 'shared-window'))
        self.assertEqual((claude['agent'], claude['model'], claude['effort'], claude['auth'], claude['credentials_file']), ('claude', 'sonnet', 'high', 'file', 'claude.json'))
        self.assertNotIn('profile', claude)
        self.assertEqual((codex['agent'], codex['model'], codex['effort'], codex['auth'], codex['credentials_file'], codex['profile']), ('codex', 'gpt-6.1-sol', 'ultra', 'auto', '', 'codex-profile'))

    def test_bundled_agent_selection_controls_legacy_preferences_without_leaking_on_override(self):
        skill = self.skill()
        path = skill / 'defaults.ini'
        path.write_text(path.read_text().replace('agent = claude', 'agent = codex'))
        with patch('agr.configuration.SKILL', skill):
            selected = configuration.load_review(self.repo)
            self.assertEqual((selected['agent'], selected['model']), ('codex', 'gpt-6.1-sol'))
            self.write_settings({'model': 'legacy-codex', 'effort': 'medium'}, self.global_config)
            selected = configuration.load_review(self.repo)
            self.assertEqual((selected['model'], selected['effort']), ('legacy-codex', 'medium'))
            selected = configuration.load_review(self.repo, {'agent': 'claude'})
            self.assertEqual((selected['agent'], selected['model'], selected['effort']), ('claude', 'sonnet', 'high'))
            self.write_settings({'agent': 'claude'})
            selected = configuration.load_review(self.repo)
            self.assertEqual((selected['agent'], selected['model']), ('claude', 'sonnet'))

    def test_preflight_shows_effective_settings_without_mutating_configuration(self):
        managed = self.fake_runtime()
        self.write_settings({'model': 'sonnet', 'effort': 'medium'}, self.global_config)
        config = self.write_settings({'runtime': 'native', 'credentials_file': managed['credentials_file'], 'effort': 'high'})
        original = config.read_bytes()
        with patch('agr.runtime.shutil.which', side_effect=lambda name, **kwargs: '/tools/' + name), patch('agr.cli.runtime.setup') as setup:
            result = self.command('preflight', '--base', 'base')
            self.assertEqual((result['model'], result['effort'], result['runtime'], result['auth']), ('sonnet', 'high', 'native', 'file'))
            self.assertEqual(result['global_config'], str(self.global_config))
            self.assertEqual(result['defaults'], str(configuration.SKILL / 'defaults.ini'))
            self.assertEqual(result['worktree_config'], str(config))
            self.assertEqual(result['credentials_file'], managed['credentials_file'])
            text = self.command('preflight', '--base', 'base', '--human')
            self.assertIn('model: sonnet', text)
            self.assertIn('runtime: native', text)
            self.assertIn('auth: file', text)
            self.assertIn(str(config), text)
            setup.assert_not_called()
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual(self.journal.rows('rounds'), [])

    def test_historical_status_does_not_invent_defaults(self):
        record = self.prepared()
        self.journal.update_round(record['id'], model=None, effort=None, preset=None)
        text = progress.describe(self.journal, record['id'])
        self.assertIn('Model: not recorded | effort: not recorded | preset: not recorded', text)

    def test_global_defaults_worktree_and_prepare_precedence(self):
        self.write_settings({'model': 'sonnet', 'effort': 'medium', 'runtime': 'native'}, self.global_config)
        self.assertEqual(configuration.load_review(self.repo)['model'], 'sonnet')
        self.assertEqual(runtime.configuration(self.repo)['runtime'], 'native')
        self.write_settings({'effort': 'high', 'runtime': 'docker'})
        self.assertEqual(runtime.configuration(self.repo)['runtime'], 'docker')
        with patch('agr.cli.runtime.setup', return_value=self.fake_runtime()):
            record = self.command('prepare', '--model', 'opus')
        self.assertEqual((record['model'], record['effort']), ('opus', 'high'))
        self.assertEqual(configuration.global_settings()['effort'], 'medium')
        self.assertEqual(configuration.load_review(self.repo)['model'], 'sonnet')
        self.write_settings({})
        self.assertEqual(configuration.load_review(self.repo)['effort'], 'medium')
        self.write_settings({'model': 'sonnet'}, self.global_config)
        self.assertEqual(configuration.load_review(self.repo)['effort'], 'xhigh')
        self.assertEqual(runtime.configuration(self.repo)['runtime'], 'docker')

    def test_global_custom_preset_with_absolute_path_works_across_worktrees(self):
        custom = self.preset(self.repo / 'custom')
        self.write_settings({'preset': str(custom)}, self.global_config)
        self.assertEqual(configuration.load_review(Path(self.temporary.name))['preset'], str(custom))
        self.assertEqual(configuration.load_review(self.repo)['preset'], str(custom))

    def test_runtime_defaults_are_loaded_from_ini_and_can_be_overridden(self):
        skill = self.skill()
        defaults = skill / 'defaults.ini'
        defaults.write_text(defaults.read_text().replace('runtime = docker', 'runtime = native').replace('auth = auto', 'auth = file'))
        with patch('agr.configuration.SKILL', skill):
            self.assertEqual(runtime.configuration(self.repo)['runtime'], 'native')
            self.write_settings({'runtime': 'docker'})
            self.assertEqual(runtime.configuration(self.repo)['runtime'], 'docker')

    def test_auto_auth_and_empty_optional_values_use_runtime_defaults(self):
        self.write_settings({'runtime': 'native', 'credentials_file': '/fixture/credentials.json', 'window_name': 'repo/branch'}, self.global_config)
        with patch('agr.runtime.platform.system', return_value='Darwin'), patch('agr.keychain.present', return_value=True):
            self.assertEqual(runtime.configuration(self.repo)['auth'], 'file')
            self.write_settings({'credentials_file': '', 'window_name': ''})
            settings = runtime.configuration(self.repo)
            self.assertEqual(settings['auth'], 'keychain')
            self.assertIsNone(settings['window_name'])
            self.write_settings({'runtime': 'docker'})
            self.assertEqual(runtime.configuration(self.repo)['auth'], 'file')

    def test_ini_values_are_literal_and_relative_credentials_use_the_worktree(self):
        self.write_settings({'credentials_file': 'auth files/token%20.json', 'model': 'claude-specific-version'})
        self.assertEqual(runtime.configuration(self.repo)['credentials_file'], str(self.repo / 'auth files/token%20.json'))
        self.assertEqual(configuration.load_review(self.repo)['model'], 'claude-specific-version')

    def test_legacy_global_configuration_requires_manual_conversion(self):
        legacy = self.global_config.with_suffix('.json')
        legacy.parent.mkdir(parents=True)
        original = '{"model":"sonnet","runtime":"native"}\n'
        legacy.write_text(original)
        with patch('agr.cli.runtime.setup') as setup:
            with self.assertRaises(ReviewError) as error:
                self.command('prepare')
            setup.assert_not_called()
        self.assertIn(str(legacy), str(error.exception))
        self.assertIn(str(self.global_config), str(error.exception))
        self.assertEqual(legacy.read_text(), original)
        self.write_settings({'model': 'sonnet', 'runtime': 'native'}, self.global_config)
        self.assertEqual(configuration.load_review(self.repo)['model'], 'sonnet')
        self.assertEqual(runtime.configuration(self.repo)['runtime'], 'native')
        self.assertEqual(legacy.read_text(), original)
