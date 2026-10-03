import configparser
from pathlib import Path
import re

from . import ReviewError


SKILL = Path(__file__).resolve().parents[2]
REVIEW_KEYS = ('agent', 'model', 'effort', 'preset', 'scope')
RUNTIME_KEYS = ('runtime', 'auth', 'credentials_file', 'window_name')
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
SCOPES = ('full', 'changes')
PROMPT_FILES = {
    'review': 'reviewer/review.md', 'policy': 'reviewer/policy.md',
    'followup': 'reviewer/followup.md', 'discuss': 'author/discuss.md', 'fix': 'author/fix.md',
}


def local(repo, values=None):
    if values is None:
        values = read_ini(Path(repo) / '.agr' / 'config.ini')
    if not isinstance(values, dict) or set(values) - set(REVIEW_KEYS + RUNTIME_KEYS):
        raise ReviewError('Configuration accepts only: ' + ', '.join(REVIEW_KEYS + RUNTIME_KEYS))
    return dict(values)


def global_path():
    return Path.home() / '.local/share/sst-agent-review/config.ini'


def global_settings(values=None):
    if values is None:
        values = read_ini(global_path())
    return local(None, values)


def effective(repo, values=None, global_values=None):
    return {**defaults(), **global_settings(global_values), **local(repo, values)}


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
    if parser.sections() != ['review'] or parser.defaults():
        raise ReviewError(str(path) + ' requires one [review] section and no [DEFAULT] values')
    values = dict(parser['review'])
    keys = set(REVIEW_KEYS + RUNTIME_KEYS)
    unknown = set(values) - keys
    if unknown:
        raise ReviewError(str(path) + ': unknown settings: ' + ', '.join(sorted(unknown)))
    if required and set(values) != keys:
        raise ReviewError(str(path) + ' requires settings: ' + ', '.join(sorted(keys)))
    return values


def defaults():
    return read_ini(SKILL / 'defaults.ini', required=True)


def load_review(repo, overrides=None, values=None, global_values=None):
    settings = {key: value for key, value in effective(repo, values, global_values).items() if key in REVIEW_KEYS}
    settings.update({key: value for key, value in (overrides or {}).items() if value is not None})
    for key in REVIEW_KEYS:
        value = settings[key]
        if not isinstance(value, str) or not value.strip() or any(ord(character) < 32 for character in value):
            raise ReviewError(key + ' must be a nonempty single-line string')
        settings[key] = value.strip()
    if settings['agent'] != 'claude':
        raise ReviewError('Only the claude agent is supported; Codex launching is not implemented yet')
    if settings['effort'] not in EFFORTS:
        raise ReviewError('Effort must be one of: ' + ', '.join(EFFORTS))
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
    for part, relative in {**PROMPT_FILES, 'protocol': str(SKILL / 'prompts/reviewer/protocol.md')}.items():
        path = directory / relative
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as error:
            raise ReviewError('Cannot read preset file: ' + str(path) + ': ' + str(error)) from error
        if not text.strip():
            raise ReviewError('Preset file must not be empty: ' + str(path))
        settings[part + '_prompt'] = text
    return settings
