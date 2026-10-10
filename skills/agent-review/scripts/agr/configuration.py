import configparser
from pathlib import Path
import re

from . import ReviewError, agents


SKILL = Path(__file__).resolve().parents[2]
REVIEW_KEYS = ('agent', 'model', 'effort', 'preset', 'scope')
RUNTIME_KEYS = ('runtime', 'auth', 'credentials_file', 'window_name')
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
CODEX_EFFORTS = EFFORTS + ('ultra',)
SCOPES = ('full', 'changes')
AGENTS = tuple(agents.ADAPTERS)
COMMON_KEYS = ('agent', 'preset', 'scope', 'runtime', 'window_name')
LEGACY_KEYS = ('model', 'effort', 'auth', 'credentials_file')
PROMPT_FILES = {
    'policy': 'reviewer/policy.md', 'protocol': 'reviewer/protocol.md',
    'followup': 'reviewer/followup.md', 'discuss': 'author/discuss.md', 'fix': 'author/fix.md',
}


def local(repo, values=None):
    if values is None:
        values = read_ini(Path(repo) / '.agr' / 'config.ini')
    if not isinstance(values, dict) or set(values) - set(COMMON_KEYS + LEGACY_KEYS + AGENTS):
        raise ReviewError('Configuration accepts review settings and agent sections: ' + ', '.join(AGENTS))
    for agent in AGENTS:
        if agent in values and (not isinstance(values[agent], dict) or set(values[agent]) - set(agents.adapter(agent).OPTIONS)):
            raise ReviewError('Invalid settings in [' + agent + ']')
    return dict(values)


def global_path():
    return Path.home() / '.local/share/sst-agent-review/config.ini'


def global_settings(values=None):
    if values is None:
        values = read_ini(global_path())
    return local(None, values)


def effective(repo, values=None, global_values=None, overrides=None):
    inherited = {}
    preferences = {agent: {} for agent in AGENTS}
    owner = defaults()['agent']
    for layer in (global_settings(global_values), local(repo, values)):
        owner = layer.get('agent', owner)
        owner = owner.strip() if isinstance(owner, str) else owner
        agents.adapter(owner)
        inherited.update({key: layer[key] for key in COMMON_KEYS if key in layer})
        preferences[owner].update({key: layer[key] for key in LEGACY_KEYS if key in layer})
        for agent in AGENTS:
            preferences[agent].update(layer.get(agent, {}))
    overrides = {key: value for key, value in (overrides or {}).items() if value is not None}
    agent = overrides.get('agent', owner)
    agent = agent.strip() if isinstance(agent, str) else agent
    agents.adapter(agent)
    return {**defaults(agent), **inherited, **preferences[agent], **overrides, 'agent': agent}


def read_ini(path, required=False):
    if not required and not path.exists():
        legacy = path.with_suffix('.json')
        if legacy.exists():
            raise ReviewError('Legacy configuration found: ' + str(legacy) + '. Convert its settings to key = value entries under [review] in ' + str(path) + '; see README Configuration. JSON configuration is no longer supported.')
        return {}
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding='utf-8') as source:
            parser.read_file(source)
    except (OSError, UnicodeError, configparser.Error) as error:
        raise ReviewError('Cannot read configuration: ' + str(path) + ': ' + str(error)) from error
    if not parser.sections() or set(parser.sections()) - set(('review',) + AGENTS) or parser.defaults():
        raise ReviewError(str(path) + ' accepts [review], agent sections and no [DEFAULT] values')
    values = dict(parser['review']) if parser.has_section('review') else {}
    unknown = set(values) - set(COMMON_KEYS + LEGACY_KEYS)
    if unknown:
        raise ReviewError(str(path) + ': unknown settings: ' + ', '.join(sorted(unknown)))
    for agent in AGENTS:
        if parser.has_section(agent):
            section = dict(parser[agent])
            unknown = set(section) - set(agents.adapter(agent).OPTIONS)
            if unknown:
                raise ReviewError(str(path) + ': unknown [' + agent + '] settings: ' + ', '.join(sorted(unknown)))
            values[agent] = section
    if required:
        if not set(COMMON_KEYS) <= values.keys() or set(LEGACY_KEYS) & values.keys():
            raise ReviewError(str(path) + ' requires complete shared defaults under [review] and agent settings in their own sections')
        agents.adapter(values['agent'])
        for agent in AGENTS:
            if set(values.get(agent, {})) != set(agents.adapter(agent).OPTIONS):
                raise ReviewError(str(path) + ' requires complete [' + agent + '] defaults')
    return values


def defaults_path():
    return SKILL / 'defaults.ini'


def defaults(agent=None):
    values = read_ini(defaults_path(), required=True)
    agent = values['agent'] if agent is None else agent
    agents.adapter(agent)
    return {**{key: values[key] for key in COMMON_KEYS}, **values[agent], 'agent': agent}


def read_prompt(path, source):
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as error:
        raise ReviewError('Cannot read ' + source + ' file: ' + str(path) + ': ' + str(error)) from error
    if not text.strip():
        raise ReviewError(source.capitalize() + ' file must not be empty: ' + str(path))
    return text


def skill_prompt(part):
    return read_prompt(SKILL / 'prompts' / PROMPT_FILES[part], 'skill prompt')


def load_review(repo, overrides=None, values=None, global_values=None):
    settings = {key: value for key, value in effective(repo, values, global_values, overrides).items() if key in REVIEW_KEYS}
    for key in REVIEW_KEYS:
        value = settings[key]
        if not isinstance(value, str) or not value.strip() or any(ord(character) < 32 for character in value):
            raise ReviewError(key + ' must be a nonempty single-line string')
        settings[key] = value.strip()
    efforts = agents.adapter(settings['agent']).EFFORTS
    if settings['effort'] not in efforts:
        raise ReviewError('Effort must be one of: ' + ', '.join(efforts))
    if settings['scope'] not in SCOPES:
        raise ReviewError('Scope must be one of: ' + ', '.join(SCOPES))
    preset = settings['preset']
    if re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', preset):
        directory = SKILL / 'presets' / preset
    else:
        directory = Path(preset).expanduser()
        if not directory.is_absolute():
            directory = Path(repo) / directory
        directory = directory.resolve()
        settings['preset'] = str(directory)
    settings['review_prompt'] = read_prompt(directory / 'reviewer/review.md', 'preset')
    for part in PROMPT_FILES:
        settings[part + '_prompt'] = skill_prompt(part)
    return settings
