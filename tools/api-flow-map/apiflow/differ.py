"""Compare two flow models (base branch vs. head) and explain what changed.

The comparison is structural, not textual: steps are matched by what they
*do* (kind + target + condition + outcome), so moving a block, renaming a
variable or reformatting code does not register as a change, while a new
condition around a database write, a different status code or a removed
authorization check does.

Output is a plain dict (JSON-serialisable) with, per endpoint:
- status: added / removed / modified / unchanged
- property_changes: auth, validation, params, body, status codes, ...
- flow_changes: a flat, breadcrumbed list of step-level changes with severity
- flow: the merged flow (head order, with removed steps kept in place) that
  the HTML renderer draws with added / removed / modified colouring
- risk + reasons, so a reviewer knows where to look first
"""
from __future__ import annotations

import datetime as _dt
import difflib
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .httpcodes import label as status_label
from .model import Branch, Endpoint, Model, Step

DIFF_SCHEMA_VERSION = "1.0"
SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}
IO_KINDS = ("io.db", "io.http", "io.queue", "io.cache", "io.file")
WRITE_WORDS = ("save", "insert", "update", "delete", "create", "upsert", "store", "remove", "commit", "run sql insert", "run sql update", "run sql delete", "write")

PROPERTY_SEVERITY = {
    "auth": "high", "spec_auth": "low", "validation": "high", "body": "high", "returns": "medium", "path_params": "high",
    "query_params": "medium", "headers": "medium", "status": "high", "responses": "medium", "declared_responses": "low",
    "documented": "low", "deprecated": "low", "consumes": "medium", "produces": "medium", "feature_flags": "medium",
    "rate_limit": "medium", "cache": "medium", "transactional": "medium", "timeout": "medium", "async": "low", "reactive": "low",
    "resilience": "medium", "dependencies": "low", "form": "medium", "middleware": "low", "spec_summary": "none", "operation_id": "none",
    "tags": "none", "lambda": "low", "inherited": "low", "version": "medium",
}
PROPERTY_LABEL = {
    "auth": "Authorization", "spec_auth": "Spec authorization", "validation": "Validation", "body": "Request body", "returns": "Response type",
    "path_params": "Path parameters", "query_params": "Query parameters", "headers": "Request headers", "status": "Declared status",
    "responses": "Observed status codes", "declared_responses": "Documented status codes", "documented": "Documented in spec",
    "deprecated": "Deprecated", "consumes": "Consumes", "produces": "Produces", "feature_flags": "Feature flags", "rate_limit": "Rate limiting",
    "cache": "Caching", "transactional": "Transaction", "timeout": "Timeout", "async": "Async", "reactive": "Reactive", "resilience": "Resilience",
    "dependencies": "Dependencies", "form": "Form fields", "middleware": "Middleware", "version": "API version",
}
IGNORED_PROPERTY_KEYS = {"spec_summary", "operation_id", "tags"}


def _sev_max(a: str, b: str) -> str:
    return a if SEVERITY_ORDER.get(a, 0) >= SEVERITY_ORDER.get(b, 0) else b


def _norm_prop(v: Any) -> Any:
    if isinstance(v, list):
        return sorted(str(x) for x in v)
    if isinstance(v, dict):
        return {k: _norm_prop(x) for k, x in sorted(v.items())}
    return v


def _fmt_prop(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "none"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def _strict(step: Step) -> str:
    return step.signature()


def _loose(step: Step) -> str:
    if step.kind in ("return", "throw"):
        return step.kind + "|" + (str(step.outcome.get("status") or "") if step.outcome else "") + "|" + (step.outcome.get("throws", "") if step.outcome else "")
    if step.kind in ("branch", "loop", "try"):
        return step.kind
    return step.loose_signature()


def _similarity(a: Step, b: Step) -> float:
    if a.kind != b.kind:
        return 0.0
    sa, sb = _strict(a), _strict(b)
    base = difflib.SequenceMatcher(None, sa, sb, autojunk=False).ratio()
    if a.kind in ("branch", "loop", "try"):
        ca, cb = _child_sigs(a), _child_sigs(b)
        inner = difflib.SequenceMatcher(None, ca, cb, autojunk=False).ratio() if (ca or cb) else 0.0
        cond = difflib.SequenceMatcher(None, a.condition or "", b.condition or "", autojunk=False).ratio()
        return max(base, 0.6 * cond + 0.4 * inner)
    return base


def _child_sigs(step: Step) -> List[str]:
    out: List[str] = []
    for c in step.children:
        out.append(_strict(c))
    for b in step.branches:
        out.append("branch:" + (b.condition or b.label))
        for c in b.steps:
            out.append(_strict(c))
    return out


# ------------------------------------------------------------------------------ merged nodes

class MNode:
    """One row of the merged flow. `head`/`base` hold the underlying steps."""

    def __init__(self, change: str, head: Optional[Step], base: Optional[Step]):
        self.change = change
        self.head = head
        self.base = base
        self.children: List[MNode] = []
        self.branches: List[MBranch] = []
        self.changed_fields: List[str] = []
        self.nested = {"added": 0, "removed": 0, "modified": 0}

    @property
    def step(self) -> Step:
        return self.head or self.base  # type: ignore[return-value]

    def to_dict(self) -> Dict[str, Any]:
        s = self.step
        d: Dict[str, Any] = {
            "change": self.change,
            "id": s.id,
            "kind": s.kind,
            "label": s.label,
            "auto_label": s.auto_label or s.label,
            "detail": s.detail,
            "condition": s.condition,
            "target": s.target,
            "outcome": dict(s.outcome),
            "tags": list(s.tags),
            "code": s.code,
            "location": s.location.to_dict() if s.location else None,
            "summary": s.summary,
        }
        if self.changed_fields:
            d["changed_fields"] = list(self.changed_fields)
        if self.base is not None and self.head is not None and self.change == "modified":
            b = self.base
            d["before"] = {"label": b.label, "auto_label": b.auto_label or b.label, "detail": b.detail, "condition": b.condition,
                           "outcome": dict(b.outcome), "code": b.code, "location": b.location.to_dict() if b.location else None, "target": b.target}
        if any(self.nested.values()):
            d["nested_changes"] = dict(self.nested)
        d["children"] = [c.to_dict() for c in self.children]
        d["branches"] = [b.to_dict() for b in self.branches]
        return d


class MBranch:
    def __init__(self, change: str, head: Optional[Branch], base: Optional[Branch]):
        self.change = change
        self.head = head
        self.base = base
        self.steps: List[MNode] = []
        self.changed_fields: List[str] = []

    @property
    def branch(self) -> Branch:
        return self.head or self.base  # type: ignore[return-value]

    def to_dict(self) -> Dict[str, Any]:
        b = self.branch
        d: Dict[str, Any] = {"change": self.change, "label": b.label, "condition": b.condition, "exits": b.exits,
                             "steps": [s.to_dict() for s in self.steps]}
        if self.changed_fields:
            d["changed_fields"] = list(self.changed_fields)
        if self.base is not None and self.head is not None and self.change == "modified":
            d["before"] = {"label": self.base.label, "condition": self.base.condition, "exits": self.base.exits}
        return d


# ------------------------------------------------------------------------------ alignment

def align_steps(base: Sequence[Step], head: Sequence[Step]) -> List[MNode]:
    """Merged list in head order; removed base steps are kept where they were."""
    bs = [_strict(s) for s in base]
    hs = [_strict(s) for s in head]
    sm = difflib.SequenceMatcher(None, bs, hs, autojunk=False)
    merged: List[MNode] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                merged.append(_pair(base[i], head[j]))
        elif tag == "delete":
            for i in range(i1, i2):
                merged.append(_leaf("removed", None, base[i]))
        elif tag == "insert":
            for j in range(j1, j2):
                merged.append(_leaf("added", head[j], None))
        else:  # replace: pair by loose signature / similarity, rest removed+added
            merged.extend(_pair_block(list(base[i1:i2]), list(head[j1:j2])))
    _mark_moves(merged)
    return merged


def _pair_block(bsteps: List[Step], hsteps: List[Step]) -> List[MNode]:
    used_b: set = set()
    used_h: set = set()
    pairs: List[Tuple[int, int, float]] = []
    for i, b in enumerate(bsteps):
        for j, h in enumerate(hsteps):
            if b.kind != h.kind:
                continue
            score = 1.0 if _loose(b) == _loose(h) and b.kind not in ("branch", "loop", "try") else _similarity(b, h)
            if score >= 0.55:
                pairs.append((i, j, score))
    pairs.sort(key=lambda p: (-p[2], p[0], p[1]))
    matched: Dict[int, int] = {}
    for i, j, _score in pairs:
        if i in used_b or j in used_h:
            continue
        # keep pairing monotonic so the merged order stays sensible
        if any((i2 < i and j2 > j) or (i2 > i and j2 < j) for i2, j2 in matched.items()):
            continue
        matched[i] = j
        used_b.add(i)
        used_h.add(j)
    out: List[MNode] = []
    bi = hj = 0
    while bi < len(bsteps) or hj < len(hsteps):
        if bi < len(bsteps) and bi in matched:
            j = matched[bi]
            while hj < j:
                if hj not in used_h:
                    out.append(_leaf("added", hsteps[hj], None))
                hj += 1
            out.append(_pair(bsteps[bi], hsteps[j]))
            bi += 1
            hj = j + 1
            continue
        if bi < len(bsteps):
            out.append(_leaf("removed", None, bsteps[bi]))
            bi += 1
            continue
        if hj < len(hsteps):
            if hj not in used_h:
                out.append(_leaf("added", hsteps[hj], None))
            hj += 1
    return out


def _leaf(change: str, head: Optional[Step], base: Optional[Step]) -> MNode:
    node = MNode(change, head, base)
    s = head or base
    assert s is not None
    node.children = [_leaf(change, c if head else None, c if base else None) for c in s.children]
    for b in s.branches:
        mb = MBranch(change, b if head else None, b if base else None)
        mb.steps = [_leaf(change, c if head else None, c if base else None) for c in b.steps]
        node.branches.append(mb)
    return node


def _pair(b: Step, h: Step) -> MNode:
    node = MNode("same", h, b)
    fields: List[str] = []
    if (b.condition or "") != (h.condition or ""):
        fields.append("condition")
    if json.dumps(b.outcome, sort_keys=True) != json.dumps(h.outcome, sort_keys=True):
        fields.append("outcome")
    if (b.target or "") != (h.target or "") and b.kind not in ("return", "throw", "branch", "loop", "try"):
        fields.append("target")
    elif b.kind in ("return", "throw") and _strict(b) != _strict(h) and "outcome" not in fields:
        fields.append("detail")
    if (b.auto_label or b.label) != (h.auto_label or h.label) and not fields:
        fields.append("label")
    if set(b.tags) ^ set(h.tags) and not fields:
        tag_diff = sorted(set(b.tags) ^ set(h.tags))
        if any(t in ("early-exit", "guard", "feature-flag", "auth-check", "validation", "or-fails", "has-default") or t.startswith("flag:") for t in tag_diff):
            fields.append("tags")
    node.children = align_steps(b.children, h.children)
    node.branches = align_branches(b.branches, h.branches)
    node.changed_fields = fields
    _count_nested(node)
    if fields and fields != ["label"]:
        node.change = "modified"
    elif fields == ["label"]:
        node.change = "same"   # label-only differences are cosmetic
        node.changed_fields = []
    return node


def align_branches(base: List[Branch], head: List[Branch]) -> List[MBranch]:
    used_h: set = set()
    out: List[MBranch] = []
    pending_head = list(range(len(head)))
    for b in base:
        best, best_score = -1, 0.0
        for j in pending_head:
            if j in used_h:
                continue
            h = head[j]
            if (b.condition or "") == (h.condition or "") and b.label == h.label:
                score = 1.0
            elif b.label in ("Otherwise", "Try", "Finally, always") and h.label == b.label:
                score = 0.95
            else:
                cond = difflib.SequenceMatcher(None, b.condition or b.label, h.condition or h.label, autojunk=False).ratio()
                inner = difflib.SequenceMatcher(None, [_strict(s) for s in b.steps], [_strict(s) for s in h.steps], autojunk=False).ratio() if (b.steps or h.steps) else 0.0
                score = 0.6 * cond + 0.4 * inner
            if score > best_score:
                best, best_score = j, score
        if best >= 0 and best_score >= 0.5:
            used_h.add(best)
            h = head[best]
            mb = MBranch("same", h, b)
            if (b.condition or "") != (h.condition or ""):
                mb.changed_fields.append("condition")
                mb.change = "modified"
            mb.steps = align_steps(b.steps, h.steps)
            out.append(mb)
        else:
            mb = MBranch("removed", None, b)
            mb.steps = [_leaf("removed", None, s) for s in b.steps]
            out.append(mb)
    # added branches, inserted in head order
    result: List[MBranch] = []
    hi = 0
    for mb in out:
        while hi < len(head) and hi not in used_h and (mb.head is None or head.index(mb.head) > hi):
            nb = MBranch("added", head[hi], None)
            nb.steps = [_leaf("added", s, None) for s in head[hi].steps]
            result.append(nb)
            used_h.add(hi)
            hi += 1
        result.append(mb)
        if mb.head is not None:
            hi = max(hi, head.index(mb.head) + 1)
    while hi < len(head):
        if hi not in used_h:
            nb = MBranch("added", head[hi], None)
            nb.steps = [_leaf("added", s, None) for s in head[hi].steps]
            result.append(nb)
        hi += 1
    return result


def _mark_moves(merged: List[MNode]) -> None:
    removed = {i: _strict(n.base) for i, n in enumerate(merged) if n.change == "removed" and n.base is not None}
    added = {i: _strict(n.head) for i, n in enumerate(merged) if n.change == "added" and n.head is not None}
    for ri, rsig in removed.items():
        for ai, asig in added.items():
            if rsig == asig and merged[ai].change == "added":
                merged[ai].change = "moved"
                merged[ai].base = merged[ri].base
                merged[ri].change = "moved-from"
                break


def _count_nested(node: MNode) -> None:
    def walk(nodes: List[MNode]):
        for n in nodes:
            if n.change in ("added", "removed"):
                node.nested[n.change] += 1
                continue   # the subtree is implied by its root
            if n.change in ("modified", "moved"):
                node.nested["modified"] += 1
            walk(n.children)
            for b in n.branches:
                if b.change in ("added", "removed"):
                    node.nested[b.change] += 1
                    continue
                walk(b.steps)
    walk(node.children)
    for b in node.branches:
        if b.change in ("added", "removed"):
            node.nested[b.change] += 1
            continue
        walk(b.steps)


# ------------------------------------------------------------------------------ change records + risk

def _breadcrumb(path: List[str]) -> List[str]:
    return [p for p in path if p]


def _io_word(kind: str) -> str:
    return {"io.db": "database access", "io.http": "outbound HTTP call", "io.queue": "message publish", "io.cache": "cache access", "io.file": "file/storage access"}.get(kind, kind)


def _is_write(step: Step) -> bool:
    low = (step.label or "").lower()
    return step.kind == "io.db" and any(low.startswith(w) or (" " + w + " ") in low for w in WRITE_WORDS)


def _contains_kind(node: MNode, kinds: Tuple[str, ...]) -> bool:
    s = node.step
    if s.kind in kinds:
        return True
    return any(_contains_kind(c, kinds) for c in node.children) or any(_contains_kind(c, kinds) for b in node.branches for c in b.steps)


def _severity_for(node: MNode, in_branch: bool) -> Tuple[str, str]:
    """(severity, reason) for an added/removed/modified step."""
    s = node.step
    ch = node.change
    lbl = s.label
    if ch == "moved":
        return "low", f"Moved: {lbl}"
    if ch == "modified":
        f = node.changed_fields
        if "outcome" in f and node.base is not None and node.head is not None:
            bo, ho = node.base.outcome, node.head.outcome
            bs, hs = bo.get("status"), ho.get("status")
            if bs != hs and bs and hs:
                sev = "high" if (bs < 400) != (hs < 400) or (bs < 400 and hs < 400) else "medium"
                return sev, f"Status code changed {status_label(bs)} → {status_label(hs)}" + ("" if in_branch else " on the main path")
            if bo.get("throws") != ho.get("throws"):
                return "medium", f"Error type changed: {bo.get('throws') or 'none'} → {ho.get('throws') or 'none'}"
            return "medium", f"Outcome changed: {lbl}"
        if "condition" in f and node.base is not None:
            return "medium", f"Condition changed: “{node.base.label}” → “{lbl}”"
        if "target" in f and node.base is not None:
            return "medium", f"Now calls {s.target} instead of {node.base.target}"
        if "tags" in f:
            return "low", f"Behaviour flags changed on: {lbl}"
        return "low", f"Changed: {lbl}"
    verb = "New" if ch == "added" else "Removed"
    if s.kind == "auth":
        return ("medium" if ch == "added" else "high"), f"{verb} authorization step: {lbl}"
    if s.kind == "validate":
        return ("low" if ch == "added" else "high"), f"{verb} validation: {lbl}"
    if s.kind in IO_KINDS:
        sev = "medium"
        if s.kind == "io.db" and _is_write(s):
            sev = "high" if ch == "removed" else "medium"
        return sev, f"{verb} {_io_word(s.kind)}: {lbl}"
    if s.kind in ("return", "throw"):
        st = s.outcome.get("status")
        if st:
            return ("medium" if in_branch else "high"), f"{verb} response {status_label(st)}" + (f" — {lbl}" if s.kind == "throw" else "")
        return "low", f"{verb} exit: {lbl}"
    if s.kind == "branch":
        gate = _contains_kind(node, IO_KINDS + ("return", "throw", "auth", "validate"))
        flag = " (feature flag)" if "feature-flag" in s.tags else ""
        return ("medium" if gate else "low"), f"{verb} condition{flag}: {lbl}{_inner_summary(node)}"
    if s.kind == "try":
        return "medium", f"{verb} error handling: {lbl}{_inner_summary(node)}"
    if s.kind == "loop":
        return ("medium" if _contains_kind(node, IO_KINDS) else "low"), f"{verb} loop: {lbl}{_inner_summary(node)}"
    if s.kind == "call":
        if _contains_kind(node, IO_KINDS):
            return "medium", f"{verb} call with side effects: {lbl}{_inner_summary(node)}"
        return "low", f"{verb} call: {lbl}"
    if s.kind == "external":
        return "low", f"{verb} external call: {lbl}"
    return "low", f"{verb}: {lbl}"


def collect_changes(nodes: List[MNode], path: List[str], out: List[Dict[str, Any]], in_branch: bool = False) -> None:
    for n in nodes:
        s = n.step
        if n.change in ("added", "removed", "modified", "moved"):
            sev, reason = _severity_for(n, in_branch)
            rec: Dict[str, Any] = {
                "type": n.change, "kind": s.kind, "path": _breadcrumb(path), "label": s.label, "detail": s.detail,
                "severity": sev, "reason": reason, "step_id": s.id,
                "location": s.location.to_dict() if s.location else None,
            }
            if n.change == "modified" and n.base is not None:
                rec["before"] = {"label": n.base.label, "condition": n.base.condition, "outcome": dict(n.base.outcome), "detail": n.base.detail, "target": n.base.target}
                rec["after"] = {"label": s.label, "condition": s.condition, "outcome": dict(s.outcome), "detail": s.detail, "target": s.target}
                rec["changed_fields"] = list(n.changed_fields)
            out.append(rec)
            if n.change in ("added", "removed"):
                # children of an added/removed step are implied; note only IO inside for the reason list
                continue
        collect_changes(n.children, path + [s.label], out, in_branch)
        for bi, b in enumerate(n.branches):
            first_if_arm = (s.kind == "branch" and bi == 0 and (b.branch.label == s.label or (n.base is not None and b.base is not None and b.base.label == n.base.label)))
            if first_if_arm and b.change == "modified":
                collect_changes(b.steps, path + [s.label], out, True)
                continue
            if b.change in ("added", "removed"):
                gate = any(_contains_kind(c, IO_KINDS + ("return", "throw")) for c in b.steps)
                tmp = MNode(b.change, s, s)
                tmp.children = list(b.steps)
                out.append({"type": b.change, "kind": "branch-arm", "path": _breadcrumb(path + [s.label]), "label": b.branch.label, "detail": b.branch.condition,
                            "severity": "medium" if gate else "low", "reason": f"{'New' if b.change == 'added' else 'Removed'} branch: {b.branch.label}{_inner_summary(tmp)}",
                            "step_id": s.id, "location": s.location.to_dict() if s.location else None})
                continue
            if b.change == "modified" and b.base is not None:
                out.append({"type": "modified", "kind": "branch-arm", "path": _breadcrumb(path + [s.label]), "label": b.branch.label, "detail": b.branch.condition,
                            "severity": "medium", "reason": f"Condition changed: “{b.base.label}” → “{b.branch.label}”", "step_id": s.id,
                            "before": {"label": b.base.label, "condition": b.base.condition}, "after": {"label": b.branch.label, "condition": b.branch.condition},
                            "location": s.location.to_dict() if s.location else None})
            collect_changes(b.steps, path + [s.label] + ([] if first_if_arm else [b.branch.label]), out, True)


def _inner_summary(node: MNode, limit: int = 3) -> str:
    """Short list of what a new/removed block contains, for the reason text."""
    found: List[str] = []

    def walk(nodes: List[MNode]):
        for c in nodes:
            st = c.step
            if st.kind in IO_KINDS + ("return", "throw", "auth", "validate", "external"):
                if st.label not in found:
                    found.append(st.label)
            walk(c.children)
            for br in c.branches:
                walk(br.steps)
    walk(node.children)
    for br in node.branches:
        walk(br.steps)
    if not found:
        return ""
    shown = found[:limit]
    more = f" (+{len(found) - limit} more)" if len(found) > limit else ""
    return " → " + "; ".join(shown) + more


def property_changes(base: Endpoint, head: Endpoint) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    keys = sorted((set(base.properties) | set(head.properties)) - IGNORED_PROPERTY_KEYS)
    for k in keys:
        bv, hv = base.properties.get(k), head.properties.get(k)
        if _norm_prop(bv) == _norm_prop(hv):
            continue
        sev = PROPERTY_SEVERITY.get(k, "low")
        if k == "auth":
            if not bv and hv:
                reason = f"Authorization added: {_fmt_prop(hv)}"
                sev = "medium"
            elif bv and not hv:
                reason = f"Authorization removed (was {_fmt_prop(bv)})"
            else:
                reason = f"Authorization changed: {_fmt_prop(bv)} → {_fmt_prop(hv)}"
        elif k == "validation":
            reason = ("Validation removed: " + _fmt_prop(bv)) if bv and not hv else ("Validation added: " + _fmt_prop(hv) if not bv else f"Validation changed: {_fmt_prop(bv)} → {_fmt_prop(hv)}")
            if not bv and hv:
                sev = "low"
        elif k in ("path_params", "query_params", "headers"):
            b_set, h_set = set(map(str, bv or [])), set(map(str, hv or []))
            removed, added = sorted(b_set - h_set), sorted(h_set - b_set)
            parts = []
            if added:
                parts.append("added " + ", ".join(added))
            if removed:
                parts.append("removed " + ", ".join(removed))
            reason = f"{PROPERTY_LABEL.get(k, k)}: " + "; ".join(parts)
            if not removed and k != "path_params":
                sev = "low" if all(a.endswith("?") for a in added) else "medium"
        elif k == "responses":
            b_set, h_set = set(bv or []), set(hv or [])
            parts = []
            if h_set - b_set:
                parts.append("now also " + ", ".join(status_label(c) for c in sorted(h_set - b_set)))
            if b_set - h_set:
                parts.append("no longer " + ", ".join(status_label(c) for c in sorted(b_set - h_set)))
            reason = "Status codes: " + "; ".join(parts)
            succ_b = {c for c in b_set if c < 300}
            succ_h = {c for c in h_set if c < 300}
            sev = "high" if succ_b and succ_h and succ_b != succ_h else "medium"
        elif k == "status":
            reason = f"Declared status changed: {status_label(bv) if bv else '—'} → {status_label(hv) if hv else '—'}"
        elif k == "documented":
            reason = "Now documented in the OpenAPI spec" if hv else "No longer documented in the OpenAPI spec"
        elif k == "deprecated":
            reason = "Marked deprecated" if hv else "No longer deprecated"
            sev = "medium" if hv else "low"
        elif k == "feature_flags":
            b_set, h_set = set(map(str, bv or [])), set(map(str, hv or []))
            parts = []
            if h_set - b_set:
                parts.append("now gated by " + ", ".join(sorted(h_set - b_set)))
            if b_set - h_set:
                parts.append("no longer gated by " + ", ".join(sorted(b_set - h_set)))
            reason = "Feature flags: " + "; ".join(parts)
        else:
            reason = f"{PROPERTY_LABEL.get(k, k.replace('_', ' ').title())}: {_fmt_prop(bv)} → {_fmt_prop(hv)}"
        out.append({"key": k, "label": PROPERTY_LABEL.get(k, k), "before": bv, "after": hv, "severity": sev, "reason": reason})
    return out


# ------------------------------------------------------------------------------ endpoints

def _endpoint_index(model: Model) -> Dict[str, Endpoint]:
    return {e.id: e for e in model.endpoints}


def _handler_key(e: Endpoint) -> str:
    return f"{e.handler.file}::{e.handler.name}" if e.handler and e.handler.name else ""


def diff_models(base: Model, head: Model, base_info: Optional[Dict[str, Any]] = None, head_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    b_idx, h_idx = _endpoint_index(base), _endpoint_index(head)
    ids = sorted(set(b_idx) | set(h_idx), key=lambda i: (i.split(" ", 1)[1].lower() if " " in i else i, i))
    # route renames: same handler, different id
    b_only = {i for i in b_idx if i not in h_idx}
    h_only = {i for i in h_idx if i not in b_idx}
    renamed: Dict[str, str] = {}
    for bi in sorted(b_only):
        key = _handler_key(b_idx[bi])
        if not key:
            continue
        for hi in sorted(h_only):
            if hi in renamed.values():
                continue
            if _handler_key(h_idx[hi]) == key:
                renamed[bi] = hi
                break

    results: List[Dict[str, Any]] = []
    summary = {"endpoints_added": 0, "endpoints_removed": 0, "endpoints_modified": 0, "endpoints_unchanged": 0,
               "steps_added": 0, "steps_removed": 0, "steps_modified": 0, "risk": "none", "risk_reasons": []}
    seen_head: set = set()
    for eid in ids:
        if eid in seen_head:
            continue
        b = b_idx.get(eid)
        h = h_idx.get(eid)
        route_change: Optional[Dict[str, Any]] = None
        if b is not None and h is None and eid in renamed:
            h = h_idx[renamed[eid]]
            seen_head.add(renamed[eid])
            route_change = {"key": "route", "label": "Route", "before": b.id, "after": h.id, "severity": "high",
                            "reason": f"Route changed: {b.id} → {h.id} (same handler) — breaking for existing clients"}
        if h is not None and b is None and eid in renamed.values():
            continue   # handled with its base partner
        rec = _diff_endpoint(b, h, route_change)
        results.append(rec)
        summary[f"endpoints_{rec['status']}"] += 1
        summary["steps_added"] += rec["counts"]["added"]
        summary["steps_removed"] += rec["counts"]["removed"]
        summary["steps_modified"] += rec["counts"]["modified"]
        summary["risk"] = _sev_max(summary["risk"], rec["risk"])

    highlights: List[Dict[str, Any]] = []
    for rec in results:
        for r in rec["risk_reasons"]:
            highlights.append({"endpoint": rec["id"], "title": rec["title"], "severity": r["severity"], "reason": r["reason"], "status": rec["status"]})
    highlights.sort(key=lambda x: (-SEVERITY_ORDER.get(x["severity"], 0), x["endpoint"]))
    summary["risk_reasons"] = highlights[:12]
    summary["changed_endpoints"] = [r["id"] for r in results if r["status"] != "unchanged"]

    return {
        "schema_version": DIFF_SCHEMA_VERSION,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "repo": head.repo or base.repo,
        "base": base_info or {"generated_at": base.generated_at, **(base.repo.get("git") or {})},
        "head": head_info or {"generated_at": head.generated_at, **(head.repo.get("git") or {})},
        "frameworks": sorted(set(base.frameworks) | set(head.frameworks)),
        "summary": summary,
        "endpoints": results,
        "diagnostics": {"base": list(base.diagnostics), "head": list(head.diagnostics)},
    }


def _diff_endpoint(b: Optional[Endpoint], h: Optional[Endpoint], route_change: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    ep = h or b
    assert ep is not None
    if b is not None and h is not None and b.source != h.source:
        if b.source == "openapi" and h.source == "code":
            rec = _diff_endpoint(None, h, None)
            rec["risk_reasons"] = [r for r in rec["risk_reasons"] if r["reason"] != "New endpoint"]
            rec["risk_reasons"].insert(0, {"severity": "medium", "reason": "Newly implemented (was only declared in the OpenAPI spec)"})
            rec["note"] = "was spec-only"
            return rec
        if b.source == "code" and h.source == "openapi":
            rec = _diff_endpoint(b, None, None)
            rec["risk_reasons"] = [{"severity": "high", "reason": "Implementation removed — the OpenAPI spec still declares this route"}]
            rec["note"] = "spec still declares it"
            return rec
    rec: Dict[str, Any] = {
        "id": ep.id, "method": ep.method, "path": ep.path, "title": ep.title or ep.auto_title, "auto_title": ep.auto_title,
        "summary": ep.summary, "handler": ep.handler.to_dict() if ep.handler else None, "framework": ep.framework, "source": ep.source,
        "properties": dict(ep.properties), "warnings": list(ep.warnings), "property_changes": [], "flow_changes": [],
        "counts": {"added": 0, "removed": 0, "modified": 0}, "risk": "none", "risk_reasons": [],
    }
    if b is None:
        rec["status"] = "added"
        rec["flow"] = [_leaf("added", s, None).to_dict() for s in ep.flow]
        rec["counts"]["added"] = _count_all(ep.flow)
        reasons = [{"severity": "medium", "reason": "New endpoint"}]
        if ep.method in ("POST", "PUT", "PATCH", "DELETE") and not ep.properties.get("auth") and ep.source == "code":
            reasons.append({"severity": "high", "reason": "New mutating endpoint with no authorization detected"})
        for s in _iter_steps(ep.flow):
            if s.kind in IO_KINDS:
                reasons.append({"severity": "medium", "reason": f"Performs {_io_word(s.kind)}: {s.label}"})
        rec["risk_reasons"] = _dedupe(reasons)[:6]
        rec["risk"] = max((r["severity"] for r in reasons), key=lambda x: SEVERITY_ORDER[x], default="medium")
        return rec
    if h is None:
        rec["status"] = "removed"
        rec["flow"] = [_leaf("removed", None, s).to_dict() for s in ep.flow]
        rec["counts"]["removed"] = _count_all(ep.flow)
        rec["risk"] = "high"
        rec["risk_reasons"] = [{"severity": "high", "reason": "Endpoint removed — breaking for existing clients"}]
        return rec
    # modified / unchanged
    merged = align_steps(b.flow, h.flow)
    rec["flow"] = [n.to_dict() for n in merged]
    changes: List[Dict[str, Any]] = []
    collect_changes(merged, [], changes)
    rec["flow_changes"] = changes
    props = property_changes(b, h)
    if route_change:
        props.insert(0, route_change)
    rec["property_changes"] = props
    rec["before"] = {"title": b.title or b.auto_title, "properties": dict(b.properties), "handler": b.handler.to_dict() if b.handler else None, "id": b.id}
    for c in changes:
        if c["type"] in ("added", "removed"):
            rec["counts"][c["type"]] += 1
        else:
            rec["counts"]["modified"] += 1
    status_explained = any(c["type"] == "modified" and "Status code changed" in c["reason"] for c in changes) or \
        any(c["type"] in ("added", "removed") and c["kind"] in ("return", "throw") for c in changes)
    reasons = [{"severity": p["severity"], "reason": p["reason"]} for p in props
               if p["severity"] != "none" and not (p["key"] == "responses" and status_explained)]
    reasons += [{"severity": c["severity"], "reason": c["reason"]} for c in changes]
    if not changes and not props:
        rec["status"] = "unchanged"
        rec["risk"] = "none"
        if b.auto_title != h.auto_title:
            rec["note"] = f"handler renamed (was “{b.auto_title}”)"
        return rec
    rec["status"] = "modified"
    reasons.sort(key=lambda r: -SEVERITY_ORDER.get(r["severity"], 0))
    rec["risk_reasons"] = _dedupe(reasons)[:8]
    rec["risk"] = max((r["severity"] for r in reasons), key=lambda x: SEVERITY_ORDER[x], default="low")
    return rec


def _dedupe(reasons: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set = set()
    out = []
    for r in reasons:
        if r["reason"] in seen:
            continue
        seen.add(r["reason"])
        out.append(r)
    return out


def _iter_steps(steps: List[Step]):
    for s in steps:
        yield s
        yield from _iter_steps(s.children)
        for b in s.branches:
            yield from _iter_steps(b.steps)


def _count_all(steps: List[Step]) -> int:
    return sum(1 for _ in _iter_steps(steps))
