"""Source-level diffs for the "Code changes" view.

The flow diff tells a reviewer *what* changed; engineers still want to see the
lines. This module asks git for unified diffs of the files that the changed
endpoints touch and turns them into hunks with old/new line numbers and
word-level highlight ranges, the way an IDE diff editor shows them.
"""
from __future__ import annotations

import difflib
import re
import subprocess
from typing import Any, Dict, Iterable, List, Optional, Tuple

_HUNK_RX = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
MAX_TOTAL_BYTES = 400_000
MAX_FILE_BYTES = 120_000


def collect(root: str, base_commit: str, head_ref: Optional[str], files: Iterable[str]) -> Dict[str, Any]:
    """Return {"files": {path: FileDiff}, "skipped": [...]}. Files are compared
    between base_commit and the working tree (or head_ref when given)."""
    wanted = sorted({f for f in files if f})
    out: Dict[str, Any] = {"files": {}, "skipped": []}
    if not wanted:
        return out
    args = ["git", "-C", root, "diff", "--no-color", "--no-ext-diff", "--unified=3", "--find-renames"]
    if head_ref:
        args.append(f"{base_commit}..{head_ref}")
    else:
        args.append(base_commit)
    args += ["--", *wanted]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=120, errors="replace")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return out
    if p.returncode not in (0, 1):
        return out
    total = 0
    for fd in parse_unified(p.stdout):
        size = sum(len(l["s"]) for h in fd["hunks"] for l in h["lines"])
        if size > MAX_FILE_BYTES:
            fd["hunks"] = []
            fd["truncated"] = True
        if total + size > MAX_TOTAL_BYTES:
            out["skipped"].append(fd["path"])
            continue
        total += size
        out["files"][fd["path"]] = fd
    # untracked new files do not appear in `git diff <commit>`; show them as added
    if not head_ref:
        try:
            u = subprocess.run(["git", "-C", root, "ls-files", "--others", "--exclude-standard", "--", *wanted], capture_output=True, text=True, timeout=60)
            for path in u.stdout.split("\n"):
                path = path.strip()
                if path and path not in out["files"]:
                    try:
                        with open(f"{root}/{path}", "r", encoding="utf-8", errors="replace") as fh:
                            text = fh.read()
                    except OSError:
                        continue
                    lines = text.split("\n")
                    if lines and lines[-1] == "":
                        lines.pop()
                    out["files"][path] = {"path": path, "old_path": None, "status": "added", "truncated": len(text) > MAX_FILE_BYTES,
                                          "hunks": [] if len(text) > MAX_FILE_BYTES else [{"header": f"@@ -0,0 +1,{len(lines)} @@", "old_start": 0, "new_start": 1,
                                                                                            "lines": [{"t": "+", "o": None, "n": i + 1, "s": l} for i, l in enumerate(lines)]}]}
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    return out


def parse_unified(text: str) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    hunk: Optional[Dict[str, Any]] = None
    old_no = new_no = 0
    if text.endswith("\n"):
        text = text[:-1]
    for raw in text.split("\n"):
        if raw.startswith("diff --git "):
            cur = {"path": "", "old_path": None, "status": "modified", "truncated": False, "hunks": []}
            files.append(cur)
            hunk = None
            m = re.match(r'^diff --git a/(.*?) b/(.*)$', raw)
            if m:
                cur["old_path"], cur["path"] = m.group(1), m.group(2)
            continue
        if cur is None:
            continue
        if raw.startswith("new file mode"):
            cur["status"] = "added"
            continue
        if raw.startswith("deleted file mode"):
            cur["status"] = "deleted"
            continue
        if raw.startswith("rename from") or raw.startswith("similarity index"):
            cur["status"] = "renamed"
            continue
        if raw.startswith("--- ") or raw.startswith("+++ ") or raw.startswith("index ") or raw.startswith("old mode") or raw.startswith("new mode") or raw.startswith("Binary files"):
            if raw.startswith("Binary files"):
                cur["status"] = "binary"
            continue
        m = _HUNK_RX.match(raw)
        if m:
            old_no, new_no = int(m.group(1)), int(m.group(3))
            hunk = {"header": raw, "old_start": old_no, "new_start": new_no, "context": m.group(5).strip(), "lines": []}
            cur["hunks"].append(hunk)
            continue
        if hunk is None:
            continue
        if raw.startswith("\\ No newline"):
            continue
        t = raw[:1] if raw else " "
        s = raw[1:] if raw else ""
        if t == "-":
            hunk["lines"].append({"t": "-", "o": old_no, "n": None, "s": s})
            old_no += 1
        elif t == "+":
            hunk["lines"].append({"t": "+", "o": None, "n": new_no, "s": s})
            new_no += 1
        else:
            hunk["lines"].append({"t": " ", "o": old_no, "n": new_no, "s": s})
            old_no += 1
            new_no += 1
    for fd in files:
        if fd["old_path"] and fd["old_path"] == fd["path"]:
            fd["old_path"] = None
        for h in fd["hunks"]:
            _word_ranges(h["lines"])
    return files


def _word_ranges(lines: List[Dict[str, Any]]) -> None:
    """Character ranges that differ between paired -/+ lines (IDE-style inline highlight)."""
    i = 0
    n = len(lines)
    while i < n:
        if lines[i]["t"] != "-":
            i += 1
            continue
        j = i
        while j < n and lines[j]["t"] == "-":
            j += 1
        k = j
        while k < n and lines[k]["t"] == "+":
            k += 1
        removed, added = lines[i:j], lines[j:k]
        for a, b in zip(removed, added):
            sm = difflib.SequenceMatcher(None, a["s"], b["s"], autojunk=False)
            if sm.ratio() < 0.6 or not a["s"].strip() or not b["s"].strip():
                continue   # unrelated lines: whole-line colouring is clearer than scattered highlights
            ra: List[Tuple[int, int]] = []
            rb: List[Tuple[int, int]] = []
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == "equal":
                    continue
                if i2 > i1:
                    ra.append((i1, i2))
                if j2 > j1:
                    rb.append((j1, j2))
            if ra:
                a["w"] = [list(r) for r in _merge(ra)]
            if rb:
                b["w"] = [list(r) for r in _merge(rb)]
        i = k


def _merge(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def files_for_diff(diff: Dict[str, Any]) -> List[str]:
    """Files worth showing: everything the changed endpoints touch."""
    wanted: set = set()

    def walk(nodes: List[Dict[str, Any]]):
        for n in nodes:
            loc = n.get("location") or {}
            if loc.get("file"):
                wanted.add(loc["file"])
            b = (n.get("before") or {}).get("location") or {}
            if b.get("file"):
                wanted.add(b["file"])
            walk(n.get("children", []))
            for br in n.get("branches", []):
                walk(br.get("steps", []))

    for ep in diff.get("endpoints", []):
        if ep.get("status") == "unchanged":
            continue
        h = ep.get("handler") or {}
        if h.get("file"):
            wanted.add(h["file"])
        bh = (ep.get("before") or {}).get("handler") or {}
        if bh.get("file"):
            wanted.add(bh["file"])
        walk(ep.get("flow", []))
    return sorted(wanted)
