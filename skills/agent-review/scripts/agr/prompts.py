from pathlib import Path
import shlex
import shutil

from . import ReviewError, read_json, write_json
from .configuration import load_review
from .source import diff, git, snapshot
from .names import round_name


SKILL = Path(__file__).resolve().parents[2]
ENTRY = SKILL / 'scripts' / 'review.py'


def previous_review(history, pass_number=None):
    return next((item for item in reversed(history['rounds']) if item['status'] == 'completed' and item.get('pass') != pass_number), None)


def history_text(history):
    lines = ['Task', '', history['session']['task'], '', 'Review rounds', '']
    for record in history['rounds']:
        lines.append(round_name(record) + ': ' + record['status'])
    lines += ['', 'Findings', '']
    for item in history['findings']:
        lines += [item['id'] + ': ' + item['title'], 'Reviewer severity: ' + item['severity'], item['body'], '']
    lines += ['Decisions, assessments, fixes and rechecks', '']
    for event in history['events']:
        label = event['kind'] + (' ' + event['finding'] if event.get('finding') else '')
        if event.get('findings'):
            label += ' ' + ', '.join(event['findings'])
        if event.get('round'):
            record = next(record for record in history['rounds'] if record['id'] == event['round'])
            label += ' by ' + round_name(record)
        lines += [label, 'At: ' + event['at']]
        for key in ('action', 'priority', 'status', 'reason', 'proposal', 'recommendation', 'summary', 'validation', 'body', 'diff'):
            if event.get(key):
                lines += [key.title() + ': ' + str(event[key])]
        for key in ('before', 'source'):
            if event.get(key, {}).get('tree'):
                lines.append(key.title() + ' snapshot: ' + event[key]['tree'])
        lines.append('')
    return '\n'.join(lines)


def prepare(journal, runtime, model=None, effort=None, parallel_with=None, preset=None, agent=None, scope=None, selection=None):
    manifest = journal.manifest
    selection = selection if selection is not None else load_review(manifest['repo'], {
        'agent': agent, 'model': model, 'effort': effort, 'preset': preset, 'scope': scope,
    })
    source = snapshot(manifest['repo'], manifest['base'])
    history = journal.export()
    previous = previous_review(history)
    anchor_inputs = None
    if parallel_with is not None:
        anchor = journal.round(parallel_with)
        previous = next((item for item in history['rounds'] if item['id'] == anchor.get('previous_review')), None)
        anchor_inputs = journal.round_directory(parallel_with) / 'input'
    if selection['scope'] == 'changes' and previous is None:
        raise ReviewError('Changes-only review needs a previous completed review; select scope full for the first run')
    record = journal.new_round(source, runtime, selection['model'], selection['effort'], parallel_with, preset=selection['preset'], scope=selection['scope'], agent=selection['agent'])
    number = record['id']
    directory = journal.round_directory(number)
    inputs = directory / 'input'
    output = directory / 'output'
    try:
        inputs.mkdir(parents=True, mode=0o700)
        (output / 'drafts').mkdir(parents=True, mode=0o700)
        reference = 'refs/agr/' + manifest['id'] + '/' + str(number)
        git(source['repo'], 'update-ref', reference, source['tree'], '')
        source['snapshot_ref'] = reference
        write_json(inputs / 'source.json', source)
        (inputs / 'full.diff').write_bytes(diff(source['repo'], source['merge_base'], source['tree']))
        (inputs / 'working.diff').write_bytes(diff(source['repo'], source['head'], source['tree']))
        if previous:
            (inputs / 'since-previous.diff').write_bytes(diff(source['repo'], previous['source']['tree'], source['tree']))
        (inputs / 'commits.txt').write_bytes(git(source['repo'], 'log', '--format=fuller', source['merge_base'] + '..' + source['head']))
        prior = history_text(history)
        previous_findings = [item['id'] for item in history['findings']]
        if anchor_inputs:
            prior = (anchor_inputs / 'history.md').read_text(encoding='utf-8')
            previous_findings = read_json(anchor_inputs / 'publication.json')['previous_findings']
        (inputs / 'history.md').write_text(prior, encoding='utf-8')
        fixes = anchor_inputs / 'fixes' if anchor_inputs else journal.directory / 'fixes'
        if fixes.is_dir():
            shutil.copytree(fixes, inputs / 'fixes')
        write_json(inputs / 'publication.json', {
            'round': number, 'reviewer': record['reviewer'], 'prefix': round_name(record),
            'previous_findings': previous_findings,
        })
        (inputs / 'reviewer.txt').write_text(round_name(record) + '\n', encoding='utf-8')
        parts = ['review', 'policy', 'protocol']
        if history['rounds'] or anchor_inputs:
            parts.append('followup')
        for part in parts:
            (inputs / (part + '.md')).write_text(selection[part + '_prompt'], encoding='utf-8')
        prompt = '\n\n'.join(selection[part + '_prompt'].rstrip() for part in parts)
        helper = shlex.join([runtime['python'], runtime['entry']])
        prompt += '\n\nYour reviewer ID: ' + round_name(record)
        prompt += '\nIdentity file: ' + str(inputs / 'reviewer.txt')
        prompt += '\nReview scope: ' + selection['scope']
        prompt += '\nPrimary diff: ' + str(inputs / ('full.diff' if selection['scope'] == 'full' else 'since-previous.diff'))
        prompt += '\nPrevious completed review: ' + (round_name(previous) if previous else 'none')
        prompt += '\n\nTask:\n' + manifest['task']
        prompt += '\n\nSource directory: ' + str(manifest['repo'])
        prompt += '\nInputs directory: ' + str(inputs)
        prompt += '\nOutput directory: ' + str(output)
        prompt += '\nPublication command: ' + helper + ' publish ' + shlex.quote(str(output))
        prompt += '\nSnapshot tree: ' + source['tree'] + '\nBase reference: ' + source['base_ref'] + '\nBase commit: ' + source['base'] + '\nMerge base: ' + source['merge_base'] + '\nHEAD: ' + source['head'] + '\n'
        (directory / 'prompt.md').write_text(prompt, encoding='utf-8')
        return journal.update_round(number, status='prepared', source=source, previous_review=previous['id'] if previous else None)
    except Exception as error:
        journal.update_round(number, status='failed', error=str(error))
        raise
