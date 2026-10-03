import configparser
from pathlib import Path
import re

from . import ReviewError, read_json


SKILL = Path(__file__).resolve().parents[2]
REVIEW_KEYS = ('agent', 'model', 'effort', 'preset', 'scope')
RUNTIME_KEYS = ('runtime', 'credentials_file', 'window_name')
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
SCOPES = ('full', 'changes')
PROMPT_FILES = {
    'review': 'reviewer/review.md', 'policy': 'reviewer/policy.md',
    'followup': 'reviewer/followup.md', 'discuss': 'author/discuss.md', 'fix': 'author/fix.md',
}


def local(repo, values=None):
    if values is None:
        path = Path(repo) / '.agr' / 'config.json'
        values = read_json(path) if path.is_file() else {}
    if not isinstance(values, dict) or set(values) - set(REVIEW_KEYS + RUNTIME_KEYS):
        raise ReviewError('Replace legacy launcher configuration with configure; user launchers are no longer supported')
    return dict(values)


def defaults():
    path = SKILL / 'defaults.ini'
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding='utf-8') as source:
            parser.read_file(source)
    except (OSError, configparser.Error) as error:
        raise ReviewError('Cannot read skill defaults: ' + str(path) + ': ' + str(error)) from error
    if parser.sections() != ['review'] or parser.defaults() or set(parser['review']) != set(REVIEW_KEYS):
        raise ReviewError('defaults.ini requires one [review] section with ' + ', '.join(REVIEW_KEYS))
    return dict(parser['review'])


def load_review(repo, overrides=None, values=None):
    settings = defaults()
    settings.update({key: value for key, value in local(repo, values).items() if key in REVIEW_KEYS})
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
