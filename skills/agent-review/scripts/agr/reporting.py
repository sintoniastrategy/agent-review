from pathlib import Path
import re

from . import ReviewError, now, read_json, write_json
from .documents import draft, locked, read_document, write_document, FINDING_FIELDS
from . import names


def files(output):
    output = Path(output)
    return sorted(list((output / 'findings').glob('*.md')) + list((output / 'checks').glob('*.md')) + ([output / 'report.md'] if (output / 'report.md').exists() else []))


def read_result(path, context):
    path = Path(path)
    value = read_document(path)
    if path.parent.name == 'findings':
        if not value['id'].startswith(context['prefix'] + '-f'):
            raise ReviewError(str(path) + ': finding belongs to another reviewer')
        if value.get('related_to') and value['related_to'] not in context['previous_findings']:
            raise ReviewError(str(path) + ': unknown related finding')
    if path.parent.name == 'checks' and value['finding'] not in context['previous_findings']:
        raise ReviewError(str(path) + ': only previous findings can be rechecked')
    return {**value, 'round': context['round'], 'reviewer': context['reviewer']}


def validate(output, context):
    for path in files(output):
        read_result(path, context)


def complete(output):
    output = Path(output)
    context = read_json(output.parent / 'input' / 'publication.json')
    marker = read_json(output / 'complete.json')
    if marker.get('round') != context['round'] or not marker.get('finished_at'):
        raise ReviewError(str(output / 'complete.json') + ': invalid completion marker')
    return marker


def publish(output, kind, source=None):
    output = Path(output).resolve()
    context = read_json(output.parent / 'input' / 'publication.json')
    with locked(output / '.lock'):
        if (output / 'complete.json').exists():
            if kind == 'finish':
                return complete(output)
            raise ReviewError('This reviewer has already finished')
        if read_json(output.parent / 'status.json')['status'] != 'running':
            raise ReviewError('Only a running reviewer can publish new results')
        if kind == 'finish':
            report = output / 'report.md'
            if not report.is_file() or not report.read_text(encoding='utf-8').strip():
                raise ReviewError('Save a short report.md before finishing')
            result = {'round': context['round'], 'finished_at': now()}
            write_json(output / 'complete.json', result)
            return result
        source = Path(source).resolve()
        if kind == 'check' and source.parent == output / 'checks' or kind == 'report' and source == output / 'report.md':
            return read_result(source, context)
        if source.parent != output / 'drafts' or not re.fullmatch(r'[A-Za-z0-9_-]+\.md', source.name):
            raise ReviewError('Write a Markdown draft in ' + str(output / 'drafts'))
        try:
            values = draft(source, kind)
        except (ReviewError, ValueError) as error:
            raise ReviewError(str(source) + ': ' + str(error)) from error
        data = {'at': now(), 'draft': source.name, **values}
        if kind == 'finding':
            if values.get('related_to') and values['related_to'] not in context['previous_findings']:
                raise ReviewError('Related-To must refer to a previous finding')
            data.setdefault('severity', 'unclassified')
            data.setdefault('title', values['body'].splitlines()[0][:160])
            paths = list((output / 'findings').glob('*.md'))
            for path in paths:
                existing = read_result(path, context)
                if existing.get('draft') == source.name:
                    fields = FINDING_FIELDS + ('body',)
                    if any(existing.get(key) != data.get(key) for key in fields):
                        raise ReviewError('An already published draft was changed; use a new draft')
                    return existing
            paths += list((output.parent / 'imports').glob('*.md'))
            index = max((names.parse_finding(path.stem)[3] for path in paths), default=0) + 1
            path = output / 'findings' / (names.finding(context['prefix'], index) + '.md')
        elif kind == 'check':
            if values['finding'] not in context['previous_findings']:
                raise ReviewError('Only findings from earlier rounds can be rechecked')
            path = output / 'checks' / (values['finding'] + '.md')
            if path.exists():
                existing = read_result(path, context)
                if existing['status'] == values['status'] and existing['reason'] == values['reason']:
                    return existing
                raise ReviewError(str(path) + ': recheck already exists; ask the author to reconcile the different conclusions')
        elif kind == 'report':
            path = output / 'report.md'
            if path.exists():
                existing = read_result(path, context)
                if existing['body'] == values['body']:
                    return existing
                raise ReviewError(str(path) + ': report already exists; inspect it with the author')
        else:
            raise ReviewError('Unknown publication kind')
        write_document(path, data)
        return read_result(path, context)
