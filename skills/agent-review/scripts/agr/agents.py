from . import ReviewError
from . import claude, codex


ADAPTERS = {'claude': claude, 'codex': codex}


def adapter(name):
    try:
        return ADAPTERS[name]
    except (KeyError, TypeError):
        raise ReviewError('Agent must be one of: ' + ', '.join(ADAPTERS)) from None
