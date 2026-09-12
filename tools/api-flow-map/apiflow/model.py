"""Framework-neutral flow model.

Every extractor and tracer produces these objects; the differ and renderers
consume them. Keeping the model small and explicit is what makes the branch
diff stable: IDs are derived from *structure* (kind + normalized detail +
position among siblings), never from line numbers, so moving code around or
adding a blank line does not register as a change.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.0"

# Step kinds. Keep this list in sync with references/model-schema.md.
KINDS = (
    "call",       # call into code that lives in this repo (may have children)
    "external",   # call into a dependency/unknown code we could not expand
    "io.db",      # database / persistence access
    "io.http",    # outbound HTTP / RPC call
    "io.queue",   # message queue / event publish or consume
    "io.cache",   # cache read / write
    "io.file",    # filesystem / object storage
    "branch",     # if / else-if / else / switch / when / ternary
    "loop",       # for / while / foreach
    "try",        # try / catch / finally
    "return",     # response / return value
    "throw",      # raised exception / error path
    "validate",   # input validation
    "auth",       # authentication / authorization check
    "transform",  # mapping / conversion / serialization
    "log",        # logging (hidden by default in renderers)
    "note",       # free-form (used for warnings like "body not traced")
)

_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Collapse whitespace so cosmetic edits do not change signatures."""
    return _WS.sub(" ", (text or "").strip())


def short_hash(*parts: str, length: int = 10) -> str:
    h = hashlib.sha1("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()
    return h[:length]


@dataclass
class Location:
    file: str = ""
    line: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"file": self.file, "line": self.line}


@dataclass
class Branch:
    """One arm of a branch/try step (e.g. `if`, `else`, `case X`, `catch E`)."""
    label: str                   # human: "if", "else", "case CANCELLED", "catch NotFound"
    condition: str = ""          # raw condition text, "" for else/default/finally
    steps: List["Step"] = field(default_factory=list)
    exits: bool = False          # True when every path in this arm returns/throws

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "condition": self.condition,
            "exits": self.exits,
            "steps": [s.to_dict() for s in self.steps],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Branch":
        return Branch(
            label=d.get("label", ""),
            condition=d.get("condition", ""),
            exits=bool(d.get("exits", False)),
            steps=[Step.from_dict(s) for s in d.get("steps", [])],
        )


@dataclass
class Step:
    kind: str
    label: str                                  # human-readable title (auto or Claude-written)
    detail: str = ""                            # technical: "OrderService.validate(order)"
    id: str = ""                                # stable structural id (filled by finalize_ids)
    location: Optional[Location] = None
    code: str = ""                              # short, redacted source snippet
    condition: str = ""                         # for branch/loop steps
    target: str = ""                            # resolved callee "Class.method" / repository etc.
    outcome: Dict[str, Any] = field(default_factory=dict)  # {"status": 404, "throws": "NotFoundException"}
    branches: List[Branch] = field(default_factory=list)
    children: List["Step"] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)          # "early-exit", "unresolved", "recursion", "depth-limit"
    auto_label: str = ""                        # keeps the heuristic label when Claude overrides `label`
    summary: str = ""                           # optional one-liner (Claude)

    # ---- structural identity -------------------------------------------------
    def signature(self) -> str:
        """What the differ compares. Deliberately excludes labels, code and
        locations so renames of the *description* or line shifts are not diffs,
        while changes to the *behaviour* (target, condition, outcome) are."""
        parts = [self.kind, normalize_text(self.target or self.detail)]
        if self.kind in ("branch", "loop"):
            parts.append(normalize_text(self.condition))
        if self.outcome:
            parts.append(json.dumps(self.outcome, sort_keys=True))
        return "|".join(parts)

    def loose_signature(self) -> str:
        """Used to pair 'modified' steps: same kind + same target, condition may differ."""
        return "|".join([self.kind, normalize_text(self.target or self.detail)])

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "detail": self.detail,
        }
        if self.auto_label and self.auto_label != self.label:
            d["auto_label"] = self.auto_label
        if self.summary:
            d["summary"] = self.summary
        if self.location:
            d["location"] = self.location.to_dict()
        if self.code:
            d["code"] = self.code
        if self.condition:
            d["condition"] = self.condition
        if self.target:
            d["target"] = self.target
        if self.outcome:
            d["outcome"] = self.outcome
        if self.tags:
            d["tags"] = list(self.tags)
        if self.branches:
            d["branches"] = [b.to_dict() for b in self.branches]
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Step":
        loc = d.get("location")
        return Step(
            kind=d.get("kind", "note"),
            label=d.get("label", ""),
            detail=d.get("detail", ""),
            id=d.get("id", ""),
            location=Location(loc.get("file", ""), int(loc.get("line", 0))) if loc else None,
            code=d.get("code", ""),
            condition=d.get("condition", ""),
            target=d.get("target", ""),
            outcome=dict(d.get("outcome", {}) or {}),
            branches=[Branch.from_dict(b) for b in d.get("branches", [])],
            children=[Step.from_dict(c) for c in d.get("children", [])],
            tags=list(d.get("tags", []) or []),
            auto_label=d.get("auto_label", "") or d.get("label", ""),
            summary=d.get("summary", ""),
        )


@dataclass
class Handler:
    name: str = ""          # "OrderController.getOrder"
    file: str = ""
    line: int = 0
    language: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Endpoint:
    method: str
    path: str
    handler: Handler = field(default_factory=Handler)
    framework: str = ""
    source: str = "code"            # code | openapi | serverless
    title: str = ""                 # human title (auto or Claude)
    summary: str = ""               # one-line description (Claude)
    auto_title: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)
    flow: List[Step] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    id: str = ""

    def compute_id(self) -> str:
        return f"{self.method.upper()} {normalize_path(self.path)}"

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id or self.compute_id(),
            "method": self.method.upper(),
            "path": self.path,
            "title": self.title,
            "handler": self.handler.to_dict(),
            "framework": self.framework,
            "source": self.source,
            "properties": self.properties,
            "flow": [s.to_dict() for s in self.flow],
        }
        if self.auto_title and self.auto_title != self.title:
            d["auto_title"] = self.auto_title
        if self.summary:
            d["summary"] = self.summary
        if self.warnings:
            d["warnings"] = list(self.warnings)
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Endpoint":
        h = d.get("handler", {}) or {}
        ep = Endpoint(
            method=d.get("method", "GET"),
            path=d.get("path", "/"),
            handler=Handler(h.get("name", ""), h.get("file", ""), int(h.get("line", 0)), h.get("language", "")),
            framework=d.get("framework", ""),
            source=d.get("source", "code"),
            title=d.get("title", ""),
            summary=d.get("summary", ""),
            auto_title=d.get("auto_title", "") or d.get("title", ""),
            properties=dict(d.get("properties", {}) or {}),
            flow=[Step.from_dict(s) for s in d.get("flow", [])],
            warnings=list(d.get("warnings", []) or []),
            id=d.get("id", ""),
        )
        if not ep.id:
            ep.id = ep.compute_id()
        return ep


@dataclass
class Model:
    repo: Dict[str, Any] = field(default_factory=dict)
    frameworks: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    endpoints: List[Endpoint] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)
    digest: List[str] = field(default_factory=list)     # Claude-written headline notes (optional)
    generated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "repo": self.repo,
            "frameworks": self.frameworks,
            "stats": self.stats,
            "digest": self.digest,
            "diagnostics": self.diagnostics,
            "endpoints": [e.to_dict() for e in self.endpoints],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Model":
        return Model(
            repo=dict(d.get("repo", {}) or {}),
            frameworks=list(d.get("frameworks", []) or []),
            stats=dict(d.get("stats", {}) or {}),
            endpoints=[Endpoint.from_dict(e) for e in d.get("endpoints", [])],
            diagnostics=list(d.get("diagnostics", []) or []),
            digest=list(d.get("digest", []) or []),
            generated_at=d.get("generated_at", ""),
        )

    def dump(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)

    @staticmethod
    def load(path: str) -> "Model":
        with open(path, "r", encoding="utf-8") as fh:
            return Model.from_dict(json.load(fh))


# ---- helpers -----------------------------------------------------------------

_PARAM_STYLES = [
    (re.compile(r"\{([^}/]+)\}"), r"{\1}"),          # {id}
    (re.compile(r":(\w+)"), r"{\1}"),                 # :id  (express / fastify / gin)
    (re.compile(r"<(?:\w+:)?(\w+)>"), r"{\1}"),       # <int:id> (flask/django)
    (re.compile(r"\$\{(\w+)\}"), r"{\1}"),            # ${id}
]


def normalize_path(path: str) -> str:
    """Canonical path form so the same route is recognised across frameworks
    and across branches: `/orders/:id/` -> `/orders/{id}`."""
    p = (path or "/").strip()
    if not p.startswith("/"):
        p = "/" + p
    for rx, repl in _PARAM_STYLES:
        p = rx.sub(repl, p)
    p = re.sub(r"/{2,}", "/", p)
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def join_paths(*parts: str) -> str:
    out = ""
    for part in parts:
        if not part:
            continue
        part = part.strip()
        if not part.startswith("/"):
            part = "/" + part
        out = out.rstrip("/") + part
    return out or "/"


def finalize_ids(endpoint: Endpoint) -> None:
    """Assign structural ids to every step. Two steps get the same id only if
    they have the same signature and the same ordinal among same-signature
    siblings, under the same ancestry. This is what lets the differ say
    'this exact step moved/changed' across branches."""
    endpoint.id = endpoint.compute_id()

    def walk(steps: List[Step], ancestry: str) -> None:
        seen: Dict[str, int] = {}
        for s in steps:
            sig = s.signature()
            n = seen.get(sig, 0)
            seen[sig] = n + 1
            s.id = short_hash(ancestry, sig, str(n))
            if s.children:
                walk(s.children, s.id + "/c")
            for i, b in enumerate(s.branches):
                walk(b.steps, f"{s.id}/b{i}:{normalize_text(b.label)}")

    walk(endpoint.flow, endpoint.id)


def iter_steps(steps: List[Step]):
    """Depth-first generator over a flow (children and branches included)."""
    for s in steps:
        yield s
        yield from iter_steps(s.children)
        for b in s.branches:
            yield from iter_steps(b.steps)


def count_steps(steps: List[Step]) -> int:
    return sum(1 for _ in iter_steps(steps))
