#!/usr/bin/env bash

install_agent_review() (
    set -eu
    case "$(uname -s)" in
        Linux|Darwin) ;;
        *) echo 'agent-review requires Linux or macOS.' >&2; exit 1 ;;
    esac
    command -v curl >/dev/null 2>&1 || { echo 'Install curl, then run this command again.' >&2; exit 1; }
    command -v python3 >/dev/null 2>&1 || { echo 'Install Python 3.9+, then run this command again.' >&2; exit 1; }
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else "Python 3.9+ is required")'
    agr_bootstrap_tmp=$(mktemp -d "${TMPDIR:-/tmp}/agent-review.XXXXXX")
    trap 'rm -rf "$agr_bootstrap_tmp"' EXIT
    echo 'Downloading the agent-review installer...'
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
        --connect-timeout 10 --max-time 60 \
        https://github.com/sintoniastrategy/agent-review/releases/latest/download/install_skill.py \
        --output "$agr_bootstrap_tmp/install_skill.py"
    python3 "$agr_bootstrap_tmp/install_skill.py" "$@"
)

install_agent_review "$@"
