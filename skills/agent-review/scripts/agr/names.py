import re

from . import ReviewError, priorities


def reviewer(pass_number, agent, slot):
    if not re.fullmatch(r'[a-z]+', agent) or pass_number < 1 or slot < 1:
        raise ReviewError('Invalid reviewer identity')
    return 'r%02d-%s%d' % (pass_number, agent, slot)


def parse_reviewer(value):
    match = re.fullmatch(r'r([0-9]+)-([a-z]+)([1-9][0-9]*)', value)
    if match:
        number, agent, slot = match.groups()
        result = (int(number), agent, int(slot))
        if reviewer(*result) == value:
            return result
    raise ReviewError('Use a reviewer address such as r03-claude1')


def round_name(record):
    return reviewer(record['pass'], record['reviewer'], record['slot'])


def finding(prefix, number):
    parse_reviewer(prefix)
    if number < 1:
        raise ReviewError('Invalid finding number')
    return prefix + '-f%03d' % number


def parse_finding(value):
    match = re.fullmatch(r'(.+)-f([0-9]+)', value)
    if match:
        prefix, number = match.groups()
        identity = parse_reviewer(prefix)
        if finding(prefix, int(number)) == value:
            return (*identity, int(number))
    raise ReviewError('Use a finding ID such as r03-claude1-f015')


def finding_file_id(stem):
    identifier, separator, suffix = stem.partition('--')
    parse_finding(identifier)
    if separator and not re.fullmatch(r'p[0-4Z]--[A-Za-z0-9_-]+', suffix):
        raise ReviewError('Invalid finding filename: ' + stem)
    return identifier


def finding_filename(identifier, severity, draft_name):
    parse_finding(identifier)
    severity = priorities.canonical(severity)
    if severity not in priorities.LABELS:
        raise ReviewError('Invalid severity')
    suffix = re.sub(r'[^A-Za-z0-9_-]', '-', draft_name) or 'finding'
    filename = identifier + '--p' + severity[1:] + '--' + suffix + '.md'
    if len(filename) > 255:
        raise ReviewError('Draft name is too long for a published filename; choose a shorter draft name')
    return filename
