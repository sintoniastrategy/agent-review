from contextlib import contextmanager
import fcntl
import json
import os
import re
from pathlib import Path
import tempfile

from . import ReviewError
from . import names


TEXT_FIELDS = {
    'assessment': 'reason', 'decision': 'reason', 'verification': 'reason',
    'session_state': 'reason', 'batch_done': 'summary', 'batch_cancel': 'summary', 'fixed': 'summary',
}
FINDING_FIELDS = ('title', 'severity', 'path', 'line', 'start_line', 'side', 'related_to')
CHECK_STATUSES = {'resolved', 'still_present', 'changed', 'uncertain'}


@contextmanager
def locked(path, exclusive=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def atomic_text(path, text, replace=False):
    atomic_bytes(path, text.encode('utf-8'), replace=replace)


def atomic_bytes(path, data, replace=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.draft-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


def document(data):
    values = dict(data)
    field = TEXT_FIELDS.get(values.get('kind'), 'body')
    body = values.pop(field, '')
    lines = ['---'] + [key + ': ' + json.dumps(value, ensure_ascii=False) for key, value in values.items()]
    return '\n'.join(lines) + '\n---\n\n' + body


def read_document(path):
    path = Path(path)
    try:
        if path.parent.name in {'findings', 'imports'}:
            result = draft(path, 'finding', published=True)
            names.parse_finding(path.stem)
            result.update(id=path.stem, created_at=result.get('at', ''))
            result.setdefault('severity', 'unclassified')
            result.setdefault('title', result['body'].splitlines()[0][:160])
            return result
        if path.parent.name == 'checks':
            result = draft(path, 'check', published=True)
            names.parse_finding(path.stem)
            if result.get('finding', path.stem) != path.stem:
                raise ReviewError('Finding does not match the filename')
            return {**result, 'finding': path.stem, 'kind': 'verification'}
        if path.name == 'report.md':
            return {'kind': 'report', **draft(path, 'report')}
        header, separator, body = path.read_text(encoding='utf-8').partition('\n---\n\n')
        if not separator or not header.startswith('---\n'):
            raise ReviewError('Missing document header')
        result = {}
        for line in header[4:].splitlines():
            key, value = line.split(': ', 1)
            if key in result:
                raise ReviewError('Duplicate document field: ' + key)
            result[key] = json.loads(value)
        if body:
            result[TEXT_FIELDS.get(result.get('kind'), 'body')] = body
        return result
    except (ReviewError, OSError, ValueError, KeyError) as error:
        raise ReviewError(str(path) + ': ' + str(error)) from error


def write_document(path, values):
    path = Path(path)
    if path.parent.name in {'findings', 'imports', 'checks'}:
        fields = FINDING_FIELDS if path.parent.name != 'checks' else ('status',)
        fields += ('at', 'draft', 'imported_by_author')
        header = []
        for field in fields:
            if field in values:
                value = json.dumps(values[field], ensure_ascii=False) if field == 'imported_by_author' else str(values[field])
                if '\n' in value or '\r' in value:
                    raise ReviewError(field + ' must be a single line')
                header.append(field.replace('_', '-').title() + ': ' + value)
        body = values['reason'] if path.parent.name == 'checks' else values['body']
        text = '\n'.join(header) + '\n\n' + body if header else body
    elif path.name == 'report.md':
        text = values['body']
    else:
        text = document(values)
    atomic_text(path, text)


def draft(path, kind, published=False):
    text = Path(path).read_text(encoding='utf-8')
    if kind == 'report':
        if not text.strip():
            raise ReviewError('Report must not be empty')
        return {'body': text}
    allowed = set(FINDING_FIELDS) if kind == 'finding' else {'finding', 'status'}
    if published:
        allowed |= {'at', 'draft', 'imported_by_author'}
    first = text.split('\n', 1)[0].partition(':')
    if kind == 'finding' and (not first[1] or first[0].strip().lower().replace('-', '_') not in allowed):
        values = {'body': text}
        validate_finding(values)
        return values
    header, separator, body = text.partition('\n\n')
    if not separator or not body.strip():
        raise ReviewError('Write headers, a blank line, then the complete Markdown body')
    values = {}
    for line in header.splitlines():
        key, separator, value = line.partition(':')
        key = key.strip().lower().replace('-', '_')
        value = value.strip()
        if not separator or key not in allowed or key in values or not value:
            raise ReviewError('Invalid or duplicate header: ' + line)
        values[key] = value
    if kind == 'finding':
        for key in ('line', 'start_line'):
            if key in values:
                values[key] = int(values[key])
        values['body'] = body
        if 'imported_by_author' in values:
            values['imported_by_author'] = json.loads(values['imported_by_author'])
        validate_finding({key: value for key, value in values.items() if key not in {'at', 'draft', 'imported_by_author'}})
    else:
        if (not published and not values.get('finding')) or values.get('status') not in CHECK_STATUSES:
            raise ReviewError('A recheck needs a valid Status and a finding ID in its filename or Finding header')
        values['reason'] = body
    return values


def validate_finding(values):
    allowed = {'body', 'title', 'severity', 'path', 'line', 'start_line', 'side', 'related_to'}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ReviewError('Unknown finding fields')
    if not isinstance(values.get('body'), str) or not values['body'].strip():
        raise ReviewError('Finding body is required')
    for key, value in values.items():
        if key in {'line', 'start_line'}:
            if type(value) is not int or value < 1:
                raise ReviewError(key + ' must be a positive integer')
        elif not isinstance(value, str) or not value.strip():
            raise ReviewError(key + ' must be nonempty text')
    if values.get('severity', 'unclassified') not in {'P0', 'P1', 'P2', 'P3', 'info', 'unclassified'}:
        raise ReviewError('Invalid severity')
    if 'path' in values:
        path = Path(values['path'])
        if path.is_absolute() or '..' in path.parts:
            raise ReviewError('Finding paths must be repository relative')
    if ('line' in values or 'start_line' in values) and 'path' not in values:
        raise ReviewError('A line needs a file path')
    if values.get('start_line', 1) > values.get('line', 1):
        raise ReviewError('start_line must be at or before line')
    if values.get('side', 'RIGHT') not in {'LEFT', 'RIGHT'}:
        raise ReviewError('Invalid side')
