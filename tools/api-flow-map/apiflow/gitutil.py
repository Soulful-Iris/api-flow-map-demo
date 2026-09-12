"""Git helpers: branch info, default base detection, merge-base and snapshots.

Snapshots use `git archive` (read-only; never touches the working tree or the
index) so a diff run is safe to execute inside a dirty checkout or in CI.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
from typing import Dict, List, Optional


class GitError(RuntimeError):
    pass


def _run(root: str, *args: str, check: bool = True, timeout: int = 120) -> str:
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise GitError(f"git {' '.join(args)} timed out")
    if check and p.returncode != 0:
        raise GitError((p.stderr or p.stdout).strip() or f"git {' '.join(args)} failed")
    return p.stdout.strip()


def is_repo(root: str) -> bool:
    try:
        return _run(root, "rev-parse", "--is-inside-work-tree") == "true"
    except GitError:
        return False


def info(root: str) -> Dict:
    if not is_repo(root):
        return {}
    out: Dict = {}
    try:
        out["branch"] = _run(root, "rev-parse", "--abbrev-ref", "HEAD")
        out["commit"] = _run(root, "rev-parse", "--short", "HEAD")
        out["dirty"] = bool(_run(root, "status", "--porcelain", "--untracked-files=normal"))
        out["subject"] = _run(root, "log", "-1", "--pretty=%s")
    except GitError:
        pass
    return out


def default_base(root: str, configured: str = "") -> str:
    """Pick the branch a PR would target: configured > origin/HEAD > main/master/develop."""
    if configured and ref_exists(root, configured):
        return configured
    try:
        ref = _run(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        if ref:
            return ref
    except GitError:
        pass
    for cand in ("origin/main", "main", "origin/master", "master", "origin/develop", "develop", "origin/trunk", "trunk"):
        if ref_exists(root, cand):
            return cand
    raise GitError("could not determine a base branch; pass --base <ref>")


def ref_exists(root: str, ref: str) -> bool:
    try:
        _run(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
        return True
    except GitError:
        return False


def merge_base(root: str, base: str, head: str = "HEAD") -> str:
    return _run(root, "merge-base", base, head)


def describe(root: str, ref: str) -> Dict:
    try:
        return {"ref": ref, "commit": _run(root, "rev-parse", "--short", ref), "subject": _run(root, "log", "-1", "--pretty=%s", ref)}
    except GitError:
        return {"ref": ref}


def changed_files(root: str, base: str, head: Optional[str] = None) -> List[str]:
    """Files changed between base and head (or the working tree when head is None)."""
    try:
        if head:
            out = _run(root, "diff", "--name-only", f"{base}...{head}" if not base.startswith(head) else f"{base} {head}")
        else:
            out = _run(root, "diff", "--name-only", base) + "\n" + _run(root, "ls-files", "--others", "--exclude-standard")
    except GitError:
        return []
    return sorted({p for p in out.split("\n") if p.strip()})


def snapshot(root: str, ref: str, dest: Optional[str] = None) -> str:
    """Extract `ref` into a temporary directory and return its path."""
    dest = dest or tempfile.mkdtemp(prefix="apiflow-base-")
    try:
        p = subprocess.run(["git", "-C", root, "archive", "--format=tar", ref], capture_output=True, timeout=600)
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH")
    if p.returncode != 0:
        raise GitError(p.stderr.decode("utf-8", "replace").strip() or f"git archive {ref} failed")
    tar_path = os.path.join(dest, ".apiflow-snapshot.tar")
    with open(tar_path, "wb") as fh:
        fh.write(p.stdout)
    with tarfile.open(tar_path) as tf:
        members = [m for m in tf.getmembers() if not (m.name.startswith("/") or ".." in m.name.split("/"))]
        try:
            tf.extractall(dest, members=members, filter="data")  # py>=3.12
        except TypeError:
            tf.extractall(dest, members=members)
    os.remove(tar_path)
    return dest


def cleanup(path: str) -> None:
    if path and os.path.isdir(path) and os.path.basename(path).startswith("apiflow-base-"):
        shutil.rmtree(path, ignore_errors=True)
