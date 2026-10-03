import os
from pathlib import Path
import subprocess
import tempfile

from . import ReviewError
from .store import local_directory


def git(repo, *args, env=None, ok=(0,), input_data=None):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, timeout=120, input=input_data,
    )
    if result.returncode not in ok:
        raise ReviewError("git " + args[0] + ": " + result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def root(path):
    return Path(git(path, "rev-parse", "--show-toplevel").decode().strip()).resolve()


def resolve(repo, ref):
    return git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()


def snapshot(repo, base):
    repo = root(repo)
    if git(repo, "ls-files", "--", ".agr"):
        raise ReviewError(".agr must be local ignored state, not tracked source")
    local = local_directory(repo)
    if local.is_symlink():
        raise ReviewError(".agr must be a real directory inside the worktree")
    head = resolve(repo, "HEAD")
    base_sha = resolve(repo, base)
    merge_base = git(repo, "merge-base", base_sha, head).decode().strip()
    status = git(repo, "status", "--porcelain=v2", "-z", "--untracked-files=normal")
    for entry in status.split(b"\0"):
        if entry.startswith((b"1 ", b"2 ")):
            submodule = entry.split(b" ", 3)[2]
            if submodule.startswith(b"S") and submodule[2:] != b"..":
                raise ReviewError("Commit or isolate dirty submodule contents before review")
    descriptor, index = tempfile.mkstemp(prefix="index-", dir=local)
    os.close(descriptor)
    os.unlink(index)
    env = {**os.environ, "GIT_INDEX_FILE": index}
    try:
        entries = git(repo, "ls-files", "--stage", "-z")
        git(repo, "read-tree", "--empty", env=env)
        git(repo, "update-index", "-z", "--index-info", env=env, input_data=entries)
        git(repo, "add", "--all", "--", ".", env=env)
        tree = git(repo, "write-tree", env=env).decode().strip()
        changed = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--name-only", env=env)
        untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z", env=env)
        if changed or untracked or resolve(repo, "HEAD") != head:
            raise ReviewError("Source changed while taking the snapshot; no review was started")
    finally:
        for name in (index, index + ".lock"):
            if os.path.exists(name):
                os.unlink(name)
    branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", ok=(0, 1)).decode().strip()
    return {
        "repo": str(repo), "branch": branch or "HEAD", "head": head, "base_ref": base,
        "base": base_sha, "merge_base": merge_base, "tree": tree,
        "working_status": status.decode("utf-8", "replace").replace("\0", "\n"),
    }


def same_source(left, right):
    return all(left[key] == right[key] for key in ("repo", "head", "base", "merge_base", "tree"))


def diff(repo, before, after):
    return git(repo, "diff", "--no-ext-diff", "--no-textconv", "--binary", before, after, "--")


def file_at(repo, tree, path):
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not path or path.startswith("-"):
        raise ReviewError("File path must be relative to the repository")
    return git(repo, "show", tree + ":" + candidate.as_posix()).decode("utf-8", "replace")
