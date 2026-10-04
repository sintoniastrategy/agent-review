import argparse
import hashlib
from pathlib import Path
import shlex
import sys
import tempfile
import uuid

from agr import ReviewError, configuration, now, progress, runtime, tmux, write_json
from agr.cli import launch_round
from agr.prompts import prepare
from agr.source import root
from agr.store import Journal, local_directory


def run(repo, agents, mode, effort):
    cache = local_directory(repo) / '.cache'
    cache.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='smoke-', dir=cache))
    write_json(directory / 'session.json', {
        'schema': 4, 'id': uuid.uuid4().hex[:12], 'repo': str(repo), 'base': 'HEAD',
        'task': 'Check interactive startup, authentication, command execution and publication. Do not review or edit source. Do not delegate.',
        'created_at': now(),
    })
    journal = Journal(directory)
    print('Smoke artifacts: ' + str(directory), flush=True)
    for agent in agents:
        selection = configuration.load_review(repo, {
            'agent': agent, 'runtime': mode, 'effort': effort, 'scope': 'full', 'preset': 'default',
        })
        managed = runtime.setup(repo, runtime.configuration(repo, agent=agent, overrides={'runtime': mode}))
        nonce = uuid.uuid4().hex
        expected = hashlib.sha256(nonce.encode()).hexdigest()
        program = (
            'import hashlib, pathlib, sys; '
            'output = pathlib.Path(sys.argv[1]); '
            '(output / "tool-probe.txt").write_text(hashlib.sha256(' + repr(nonce) + '.encode()).hexdigest() + "\\n"); '
            '(output / "report.md").write_text("Interactive runtime smoke: shell command executed and result saved.\\n")'
        )
        command = shlex.join([managed['python'], '-c', program])
        selection['review_prompt'] = (
            'This is an explicitly requested runtime smoke check. Do not review code or run tests. '
            'Use your shell execution tool to run the following command, adding the supplied Output directory as its final argument: '
            + command + '\nThen execute the supplied Publication command with the finish subcommand. '
            'Do not ask questions, delegate or change source. If any command fails, report the failure and stop.'
        )
        record = prepare(journal, managed, selection=selection)
        number = record['id']
        launch_round(journal, number)
        status = progress.watch(journal, number)
        if status != 'completed':
            raise ReviewError(agent + ' smoke did not complete; retained artifacts: ' + str(directory))
        probe = journal.round_directory(number) / 'output' / 'tool-probe.txt'
        if not probe.is_file() or probe.read_text().strip() != expected:
            raise ReviewError(agent + ' command result is missing or incorrect; retained artifacts: ' + str(directory))
        tmux.close_session(journal, number)
        print(agent + ': startup, login, command execution, publication and cleanup passed', flush=True)
    return directory


def main():
    parser = argparse.ArgumentParser(description='Explicit live runtime smoke; starts real models and retains artifacts separately from the review cycle')
    parser.add_argument('--repo', default='.')
    parser.add_argument('--agent', choices=('claude', 'codex', 'both'), default='both')
    parser.add_argument('--runtime', choices=('auto', 'native', 'docker'), default='docker')
    parser.add_argument('--effort', choices=configuration.CODEX_EFFORTS)
    args = parser.parse_args()
    try:
        agents = ('claude', 'codex') if args.agent == 'both' else (args.agent,)
        run(root(args.repo), agents, args.runtime, args.effort)
    except (ReviewError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
