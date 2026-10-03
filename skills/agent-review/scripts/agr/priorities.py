LABELS = {'P0': 'crit', 'P1': 'high', 'P2': 'med', 'P3': 'low', 'P4': 'info', 'PZ': 'undef'}
ALIASES = {'info': 'P4', 'unclassified': 'PZ'}
VALUES = tuple(LABELS) + tuple(ALIASES)
RANKS = {value: rank for rank, value in enumerate(LABELS)}


def canonical(value):
    return ALIASES.get(value, value)


def rank(value):
    return RANKS[canonical(value)]


def label(value):
    return LABELS[canonical(value)]
