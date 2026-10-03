import os
import shutil
import stat
import textwrap
import time
from collections import Counter, defaultdict

from .store import ACTIVE
from . import tmux
from .names import parse_finding, round_name


DECISIONS = {
    'pending': 'Not discussed', 'fix': 'Approved to fix',
    'defer': 'Deferred', 'reject': 'Rejected',
}
RECHECKS = {
    'not_checked': 'Not rechecked', 'resolved': 'Confirmed resolved',
    'still_present': 'Still present', 'changed': 'Changed', 'uncertain': 'Uncertain',
}
PRIORITY_LABELS = {'P0': 'Crit', 'P1': 'High', 'P2': 'Med', 'P3': 'Low', 'info': 'Info', 'unclassified': 'Unclassified'}


def finding_label(identifier, mixed=False):
    number, agent, slot, index = parse_finding(identifier)
    return 'R%d-%sF%d' % (number, '%s%d-' % (agent.capitalize(), slot) if mixed else '', index)


def clean(text, limit=400):
    return ' '.join(''.join(character for character in text if character.isprintable() or character.isspace()).split())[:limit]


def reviewer_update(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                return 'Progress file is not a regular file', None
            return clean(stream.read(4096).decode('utf-8', errors='replace')), metadata.st_mtime
    except FileNotFoundError:
        return '', None
    except OSError:
        return 'Progress file is unavailable', None


def view(journal, number):
    record = journal.round(number)
    directory = journal.round_directory(number)
    output = directory / 'output'
    message, updated = reviewer_update(output / 'progress.txt')
    terminal = directory / 'terminal.log'
    try:
        last_output = terminal.stat().st_mtime
    except FileNotFoundError:
        last_output = None
    return {
        'record': record, 'directory': directory, 'message': message,
        'updated': updated, 'last_output': last_output,
        'findings': len(list((output / 'findings').glob('*.md'))),
        'checks': len(list((output / 'checks').glob('*.md'))),
        'report': (output / 'report.md').is_file(),
    }


def age(timestamp):
    return str(max(0, int(time.time() - timestamp))) + 's ago' if timestamp is not None else 'not yet'


def summary(value):
    record = value['record']
    phase = record['status']
    if phase == 'running':
        phase += ' (reviewing)' if record.get('prompt_sent_at') else ' (waiting for Claude prompt)'
    elif phase == 'completed' and record.get('session_open'):
        phase += ' (Claude session open)'
    label = round_name(record)
    lines = ['%s | %s | findings: %d | rechecks: %d | report: %s' % (label, phase, value['findings'], value['checks'], 'saved' if value['report'] else 'pending')]
    lines.append('Model: %s | effort: %s | preset: %s' % tuple(clean(record.get(key) or 'not recorded') for key in ('model', 'effort', 'preset')))
    lines.append('Scope: ' + record.get('scope', 'not recorded'))
    if value['message']:
        lines.append('Reviewer last update (' + age(value['updated']) + '): ' + value['message'])
    else:
        lines.append('Reviewer has not reported a stage yet.')
    lines.append('Terminal output: ' + age(value['last_output']) + '.')
    if record.get('error'):
        lines.append('Reason: ' + clean(record['error']))
    if record.get('session_error'):
        lines.append('Session cleanup: ' + clean(record['session_error']))
    return '\n'.join(lines)


def describe(journal, number):
    value = view(journal, number)
    record = value['record']
    lines = [summary(value)]
    if record.get('tmux'):
        target = record['tmux']
        lines += ['Window: ' + tmux.window_name(journal, number) + ' (' + target['window'] + ', pane ' + target['pane'] + ')', 'Attach: ' + target['attach']]
    lines.append('Artifacts: ' + str(value['directory']))
    return '\n'.join(lines)


def status(journal):
    data = journal.export()
    rounds = data['rounds']
    totals = finding_summary(data['findings'], rounds)
    if not rounds:
        return totals + '\n\nNo review rounds prepared.'
    latest_pass = rounds[-1].get('pass', rounds[-1]['id'])
    return '\n\n'.join([totals] + [describe(journal, record['id']) for record in rounds if record.get('pass', record['id']) == latest_pass])


def finding_summary(items, rounds):
    decisions = Counter(item['decision'] for item in items)
    passes = {record['id']: record['pass'] for record in rounds}
    rechecks = Counter()
    origins = defaultdict(Counter)
    for item in items:
        check = item.get('verification')
        state = check['status'] if check else 'not_checked'
        rechecks[state] += 1
        if check:
            origins[state][passes[check['round']]] += 1
    lines = ['Findings across all passes: %d' % len(items), 'Decisions and fixes:']
    for state, label in DECISIONS.items():
        lines.append('  %s: %d' % (label, decisions[state]))
    lines.append('  Fix recorded (independent of current decision): %d' % sum(bool(item.get('fix')) for item in items))
    priorities = Counter(item['priority'] for item in items)
    lines.append('Priorities: ' + ', '.join('%s: %d' % (label, priorities[value]) for value, label in PRIORITY_LABELS.items()))
    lines += ['', 'Latest recorded reviewer recheck (one per finding):']
    for state, label in RECHECKS.items():
        source = ', '.join('r%02d: %d' % (number, count) for number, count in sorted(origins[state].items()))
        lines.append('  %s: %d%s' % (label, rechecks[state], ' (' + source + ')' if source else ''))
    return '\n'.join(lines)


def findings(items, detail=False):
    if not items:
        return 'No findings.'
    identities = [parse_finding(item['id']) for item in items]
    reviewers = {(agent, slot) for _, agent, slot, _ in identities}
    lines = []
    if len(reviewers) == 1:
        agent, slot = next(iter(reviewers))
        lines.append('Reviewer: %s %d' % (agent.capitalize(), slot))
    for item, (number, agent, slot, index) in zip(items, identities):
        label = finding_label(item['id'], len(reviewers) > 1)
        lines.append('%s (P:%s) | %s | %s' % (label, PRIORITY_LABELS[item['priority']], item['decision'], clean(item['title'])))
        if detail:
            lines += ['ID: ' + item['id'], item['body'], 'Decision: ' + item.get('reason', 'Not discussed')]
            if item.get('verification'):
                lines.append('Recheck: ' + item['verification']['status'])
    return '\n'.join(lines)


def finding_table(items, rounds, width=None):
    if not items:
        return 'No findings.'
    ordered = sorted(items, key=lambda item: parse_finding(item['id']))
    identities = [parse_finding(item['id']) for item in ordered]
    reviewers = {(agent, slot) for _, agent, slot, _ in identities}
    mixed = len(reviewers) > 1
    passes = {record['id']: record['pass'] for record in rounds}
    headers = ['Finding', 'Priority', 'Title', 'Author proposes', 'Reason', 'Decision', 'Fix recorded', 'Reviewer verdict', 'Check pass']
    rows = []
    for item, (number, agent, slot, index) in zip(ordered, identities):
        check = item.get('verification')
        state = check['status'] if check else 'not_checked'
        assessment = item.get('assessment', {})
        rows.append([finding_label(item['id'], mixed),
            PRIORITY_LABELS[item['priority']], clean(item['title'], limit=None), assessment.get('recommendation') or 'Not assessed',
            clean(assessment.get('reason', '-'), limit=None), DECISIONS[item['decision']], 'Yes' if item.get('fix') else 'No',
            RECHECKS[state], 'r%02d' % passes[check['round']] if check else '-',
        ])
    widths = [max(len(row[index]) for row in [headers] + rows) for index in range(len(headers))]
    title_column = headers.index('Title')
    widths[headers.index('Reason')] = min(widths[headers.index('Reason')], 40)
    available = (width if width is not None else shutil.get_terminal_size((160, 24)).columns) - sum(size for index, size in enumerate(widths) if index != title_column) - 3 * len(headers) - 1
    widths[title_column] = min(widths[title_column], min(72, max(24, available)))
    border = '+' + '+'.join('-' * (size + 2) for size in widths) + '+'
    lines = []
    if not mixed:
        agent, slot = next(iter(reviewers))
        lines.append('Reviewer: %s %d' % (agent.capitalize(), slot))
    lines.append(border)
    for row in [headers] + rows:
        cells = [textwrap.wrap(value, width=size, break_on_hyphens=False) or [''] for value, size in zip(row, widths)]
        for index in range(max(len(cell) for cell in cells)):
            lines.append('| ' + ' | '.join((cell[index] if index < len(cell) else '').ljust(size) for cell, size in zip(cells, widths)) + ' |')
        lines.append(border)
    return '\n'.join(lines)


def watch(journal, number, emit=None):
    emit = emit or (lambda message: print(message, flush=True))
    previous = None
    last_change = last_message = time.monotonic()
    try:
        while True:
            value = view(journal, number)
            record = value['record']
            signature = (record['status'], bool(record.get('prompt_sent_at')), value['message'], value['findings'], value['checks'], value['report'], record.get('error'), record.get('session_open'), record.get('session_error'))
            moment = time.monotonic()
            if signature != previous:
                emit(describe(journal, number) if previous is None else summary(value))
                previous = signature
                last_change = last_message = moment
            elif moment - last_message >= 60:
                emit('No new stage or publication for %ds. Terminal output: %s.' % (int(moment - last_change), age(value['last_output'])))
                last_message = moment
            if record['status'] not in ACTIVE:
                return record['status']
            time.sleep(2)
    except KeyboardInterrupt:
        emit('Stopped watching; the review session is unchanged.')
        return None
