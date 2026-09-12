"""Markdown output.

- diff mode: a PR-comment-sized digest (risk, per-endpoint changes) plus a
  compact +/−/~ flow for each changed endpoint, inside <details> so the
  comment stays short.
- scan mode: one section per endpoint with properties, the flow as an indented
  list, and a Mermaid flowchart (renders on GitHub / GitLab / Confluence).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .httpcodes import label as status_label

_SEV_ICON = {"high": "🔴", "medium": "🟠", "low": "⚪", "none": "·"}
_KIND_ICON = {"io.db": "🗄", "io.http": "🌐", "io.queue": "📨", "io.cache": "⚡", "io.file": "📁", "branch": "◇", "loop": "↻", "try": "⚠",
              "return": "◀", "throw": "✕", "validate": "✓", "auth": "🔒", "transform": "⇌", "log": "≡", "external": "◌", "call": "○", "note": "…"}
_CHG = {"added": "+", "removed": "−", "modified": "~", "moved": "⇅", "same": " "}


def _md_escape(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


# ------------------------------------------------------------------------------ diff

def render_diff(d: Dict[str, Any], max_endpoints: int = 25, include_flows: bool = True) -> str:
    s = d.get("summary", {})
    base, head = d.get("base", {}), d.get("head", {})
    lines: List[str] = []
    repo = (d.get("repo") or {}).get("name", "API")
    lines.append(f"## API change review — {repo}")
    lines.append("")
    b = (base.get("ref") or base.get("branch") or "base") + (f" @{base['commit']}" if base.get("commit") else "")
    h = (head.get("branch") or head.get("ref") or "working tree") + (f" @{head['commit']}" if head.get("commit") else "") + (" (with uncommitted changes)" if head.get("dirty") else "")
    lines.append(f"`{b}` → `{h}`  ")
    lines.append(f"**Risk: {s.get('risk', 'none')}** · endpoints: +{s.get('endpoints_added', 0)} added, −{s.get('endpoints_removed', 0)} removed, "
                 f"~{s.get('endpoints_modified', 0)} modified, {s.get('endpoints_unchanged', 0)} unchanged · "
                 f"steps: +{s.get('steps_added', 0)} / −{s.get('steps_removed', 0)} / ~{s.get('steps_modified', 0)}")
    lines.append("")
    if d.get("digest"):
        lines.append(str(d["digest"]).strip())
        lines.append("")
    changed = [e for e in d.get("endpoints", []) if e.get("status") != "unchanged"]
    if not changed:
        lines.append("No endpoint behaviour changed between base and head.")
        return "\n".join(lines) + "\n"
    lines.append("| | Endpoint | Change | Risk | What changed |")
    lines.append("|---|---|---|---|---|")
    for e in changed[:max_endpoints]:
        reasons = [r["reason"] for r in e.get("risk_reasons", [])][:3]
        what = "<br>".join(_md_escape(r) for r in reasons) or "—"
        lines.append(f"| {_SEV_ICON.get(e.get('risk', 'none'), '·')} | `{e['method']} {e['path']}`<br>{_md_escape(e.get('title', ''))} | {e['status']} | {e.get('risk', 'none')} | {what} |")
    if len(changed) > max_endpoints:
        lines.append(f"| | … {len(changed) - max_endpoints} more changed endpoints | | | |")
    lines.append("")
    if include_flows:
        for e in changed[:max_endpoints]:
            lines.append(f"<details><summary><b>{e['method']} {e['path']}</b> — {_md_escape(e.get('title', ''))} ({e['status']})</summary>")
            lines.append("")
            if e.get("changes_summary"):
                lines.append(str(e["changes_summary"]).strip())
                lines.append("")
            pcs = e.get("property_changes", [])
            if pcs:
                for p in pcs:
                    lines.append(f"- {_SEV_ICON.get(p['severity'], '·')} {p['reason']}")
                lines.append("")
            fcs = e.get("flow_changes", [])
            if fcs:
                for c in fcs:
                    where = " › ".join(c.get("path", []))
                    lines.append(f"- {_SEV_ICON.get(c['severity'], '·')} {c['reason']}" + (f" _(in {where})_" if where else ""))
                lines.append("")
            flow = e.get("flow", [])
            if flow and e.get("status") in ("added", "modified"):
                lines.append("```diff")
                _flow_diff_lines(flow, lines, 0, changed_only=(e.get("status") == "modified"))
                lines.append("```")
                lines.append("")
                lines.append("```mermaid")
                lines.extend(mermaid_flowchart(e))
                lines.append("```")
            elif e.get("status") == "removed":
                lines.append("_Endpoint no longer exists in head._")
            lines.append("")
            lines.append("</details>")
            lines.append("")
    return "\n".join(lines) + "\n"


def _has_change(n: Dict[str, Any]) -> bool:
    if n.get("change", "same") != "same":
        return True
    if any(_has_change(c) for c in n.get("children", [])):
        return True
    for b in n.get("branches", []):
        if b.get("change", "same") != "same" or any(_has_change(c) for c in b.get("steps", [])):
            return True
    return False


def _flow_diff_lines(nodes: List[Dict[str, Any]], out: List[str], depth: int, changed_only: bool) -> None:
    for n in nodes:
        ch = n.get("change", "same")
        if ch == "moved-from":
            continue
        if changed_only and depth > 0 and not _has_change(n):
            continue
        prefix = _CHG.get(ch, " ")
        indent = "  " * depth
        meta = ""
        if n.get("outcome", {}).get("status"):
            meta = f" [{n['outcome']['status']}]"
        if ch == "modified" and n.get("before"):
            out.append(f"- {indent}{n['before'].get('label', '')}{(' [' + str(n['before']['outcome']['status']) + ']') if n['before'].get('outcome', {}).get('status') else ''}")
            out.append(f"+ {indent}{n.get('label', '')}{meta}")
        else:
            out.append(f"{prefix} {indent}{n.get('label', '')}{meta}")
        kids = n.get("children", [])
        if kids and (ch in ("added", "removed") or any(_has_change(c) for c in kids) or not changed_only):
            _flow_diff_lines(kids, out, depth + 1, changed_only and ch == "same")
        for i, b in enumerate(n.get("branches", [])):
            bch = b.get("change", "same")
            first_if = (i == 0 and n.get("kind") == "branch" and str(n.get("detail", "")).startswith("if ("))
            if changed_only and bch == "same" and not any(_has_change(c) for c in b.get("steps", [])):
                continue
            if not first_if:
                out.append(f"{_CHG.get(bch, ' ')} {indent}  ◇ {b.get('label', '')}")
            _flow_diff_lines(b.get("steps", []), out, depth + (1 if first_if else 2), changed_only and bch == "same")


# ------------------------------------------------------------------------------ scan

def render_scan(m: Dict[str, Any], mermaid: bool = True) -> str:
    lines: List[str] = []
    repo = (m.get("repo") or {}).get("name", "API")
    st = m.get("stats", {})
    lines.append(f"# API flow map — {repo}")
    lines.append("")
    git = (m.get("repo") or {}).get("git") or {}
    if git.get("branch"):
        lines.append(f"`{git['branch']}` @{git.get('commit', '')}{' (dirty)' if git.get('dirty') else ''} · ")
    lines.append(f"{st.get('endpoints', len(m.get('endpoints', [])))} endpoints · {st.get('steps', 0)} steps · frameworks: {', '.join(m.get('frameworks', [])) or '—'}")
    lines.append("")
    if m.get("digest"):
        lines.append(str(m["digest"]).strip())
        lines.append("")
    lines.append("| Method | Path | Title | Auth | Responds |")
    lines.append("|---|---|---|---|---|")
    for e in m.get("endpoints", []):
        p = e.get("properties", {})
        lines.append(f"| `{e['method']}` | `{e['path']}` | {_md_escape(e.get('title', ''))} | {_md_escape(', '.join(map(str, p.get('auth', []))) or '—')} | {', '.join(str(c) for c in p.get('responses', [])) or '—'} |")
    lines.append("")
    for e in m.get("endpoints", []):
        lines.append(f"## `{e['method']} {e['path']}` — {e.get('title', '')}")
        lines.append("")
        if e.get("summary"):
            lines.append(str(e["summary"]).strip())
            lines.append("")
        h = e.get("handler") or {}
        if h.get("file"):
            lines.append(f"Handler: `{h.get('name', '')}` · `{h['file']}:{h.get('line', '')}` · {e.get('framework', '')}")
        props = e.get("properties", {})
        chips = []
        for k in ("auth", "validation", "body", "path_params", "query_params", "headers", "status", "responses", "returns", "feature_flags", "documented", "deprecated"):
            v = props.get(k)
            if v in (None, [], False, ""):
                continue
            chips.append(f"**{k.replace('_', ' ')}**: {', '.join(map(str, v)) if isinstance(v, list) else v}")
        if chips:
            lines.append("  ")
            lines.append(" · ".join(chips))
        for w in e.get("warnings", []):
            lines.append(f"> ⚠ {w}")
        lines.append("")
        flow = e.get("flow", [])
        if flow:
            _flow_lines(flow, lines, 0)
            lines.append("")
            if mermaid:
                lines.append("```mermaid")
                lines.extend(mermaid_flowchart(e))
                lines.append("```")
                lines.append("")
    return "\n".join(lines) + "\n"


def _flow_lines(nodes: List[Dict[str, Any]], out: List[str], depth: int) -> None:
    for n in nodes:
        if n.get("kind") == "log":
            continue
        meta = f" `{n['outcome']['status']}`" if n.get("outcome", {}).get("status") else ""
        out.append(f"{'  ' * depth}- {_KIND_ICON.get(n.get('kind'), '·')} {n.get('label', '')}{meta}")
        if n.get("children") and not ("wrapper" in n.get("tags", [])):
            _flow_lines(n["children"], out, depth + 1)
        for i, b in enumerate(n.get("branches", [])):
            first_if = (i == 0 and n.get("kind") == "branch" and str(n.get("detail", "")).startswith("if ("))
            if not first_if:
                out.append(f"{'  ' * (depth + 1)}- ◇ **{b.get('label', '')}**")
            _flow_lines(b.get("steps", []), out, depth + (1 if first_if else 2))


def _facts(n: Dict[str, Any]) -> List[str]:
    """Variable/argument lines shown inside a diagram box."""
    out: List[str] = []
    d = str(n.get("detail") or "")
    kind = n.get("kind", "")
    if kind in ("branch", "loop"):
        if n.get("condition"):
            out.append(_clip(n["condition"], 60))
    elif kind == "return":
        m = re.match(r"^return\s+(.*)$", d, re.S)
        body = (m.group(1) if m else d).strip()
        if body and not re.match(r"^(ResponseEntity\.\w+\(\)\.build\(\)|res\.sendStatus\(\d+\))$", body):
            out.append(_clip(body, 60))
    elif kind == "throw":
        body = re.sub(r"^(throw|raise)\s+", "", d).strip()
        if body and not re.match(r"^[a-z_]\w{0,3}$", body):
            out.append(_clip(body, 60))
    elif kind != "try":
        for part in d.split(" → ")[:2]:
            i = part.find("(")
            if i > 0:
                callee = ".".join(part[:i].strip().split(".")[-2:])
                inner = part[i + 1:].strip()
                if inner.endswith(")"):
                    inner = inner[:-1]
                out.append(_clip(f"{callee}({inner})", 60))
    if n.get("change") == "modified" and n.get("before"):
        b = n["before"]
        f = n.get("changed_fields", [])
        was = b.get("condition") if "condition" in f else (b.get("label", "") + (f" [{b['outcome']['status']}]" if b.get("outcome", {}).get("status") else "")) if "outcome" in f else (b.get("target") or b.get("detail") or b.get("label"))
        if was:
            out.append("was: " + _clip(was, 60))
    for t in n.get("tags", []):
        if t.startswith("flag:"):
            out.append("flag: " + t[5:])
    return out


def _clip(text: str, n: int) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def mermaid_flowchart(e: Dict[str, Any], max_nodes: int = 60, with_facts: bool = True) -> List[str]:
    """A compact top-to-bottom flowchart of the endpoint's main path, with the
    variables involved in each box and diff colouring when the flow is merged."""
    lines = ["flowchart TD"]
    counter = {"n": 0}
    edges: List[str] = []
    classes: List[str] = []
    diff_mode = any(n.get("change") for n in e.get("flow", []))

    def nid() -> str:
        counter["n"] += 1
        return f"n{counter['n']}"

    def node(n: Dict[str, Any]) -> str:
        i = nid()
        kind = n.get("kind", "")
        parts = [_mm(n.get("label", ""))]
        if with_facts:
            parts += ["<i>" + _mm(f) + "</i>" for f in _facts(n)]
        if n.get("outcome", {}).get("status"):
            parts[0] += f" [{n['outcome']['status']}]"
        text = "<br/>".join(parts)
        if kind == "branch":
            lines.append(f'    {i}{{"{text}"}}')
        elif kind in ("return", "throw"):
            lines.append(f'    {i}(["{text}"])')
        elif kind.startswith("io."):
            lines.append(f'    {i}[("{text}")]')
        else:
            lines.append(f'    {i}["{text}"]')
        ch = n.get("change")
        if ch in ("added", "removed", "modified"):
            classes.append(f"    class {i} {ch}")
        elif kind == "throw":
            classes.append(f"    class {i} fail")
        elif kind == "return":
            classes.append(f"    class {i} resp")
        elif kind == "branch":
            classes.append(f"    class {i} cond")
        return i

    start = nid()
    lines.append(f'    {start}(["{_mm(e["method"] + " " + e["path"])}"])')
    classes.append(f"    class {start} resp")

    def walk(nodes: List[Dict[str, Any]], prev: str) -> str:
        for n in nodes:
            if counter["n"] >= max_nodes:
                return prev
            if n.get("kind") in ("log", "transform", "note") or n.get("change") == "moved-from":
                continue
            i = node(n)
            edges.append(f"    {prev} --> {i}")
            if n.get("branches"):
                ends = []
                for bi, b in enumerate(n["branches"]):
                    first_if = (bi == 0 and n.get("kind") == "branch" and str(n.get("detail", "")).startswith("if ("))
                    lab = "yes" if first_if else _mm(b.get("label", ""))[:40]
                    steps = [s for s in b.get("steps", []) if s.get("kind") not in ("log", "transform", "note") and s.get("change") != "moved-from"]
                    if steps:
                        first = node(steps[0])
                        edges.append(f'    {i} -- "{lab}" --> {first}')
                        end = walk(steps[1:], first)
                        if not b.get("exits"):
                            ends.append(end)
                    else:
                        ends.append(i)
                if n.get("kind") == "branch" and not any(b.get("label") == "Otherwise" for b in n["branches"]):
                    ends.append(i)
                if not ends:
                    return i
                # join point
                j = nid()
                lines.append(f"    {j}(( ))")
                for en in ends:
                    edges.append(f"    {en} --> {j}")
                prev = j
            elif n.get("children") and "wrapper" not in n.get("tags", []) and n.get("kind") == "call":
                end = walk(n["children"], i)
                prev = end
            else:
                prev = i
        return prev

    walk(e.get("flow", []), start)
    lines.extend(edges)
    lines.append("    classDef cond fill:#F8F6FC,stroke:#7C6F9B,color:#1B2430")
    lines.append("    classDef resp fill:#1B2430,stroke:#1B2430,color:#ffffff")
    lines.append("    classDef fail fill:#B42318,stroke:#B42318,color:#ffffff")
    if diff_mode:
        lines.append("    classDef added fill:#E8F5EE,stroke:#1E7F4F,stroke-width:2px,color:#1B2430")
        lines.append("    classDef removed fill:#FCEBEA,stroke:#B42318,stroke-width:2px,stroke-dasharray:4 3,color:#8C3A33")
        lines.append("    classDef modified fill:#FFF3E0,stroke:#B25E09,stroke-width:2px,color:#1B2430")
    lines.extend(classes)
    return lines


def _mm(text: str) -> str:
    t = re.sub(r"[\"`]", "'", text or "")
    t = t.replace("||", " or ").replace("&&", " and ").replace("|", "/")
    t = t.replace("[", "(").replace("]", ")").replace("{", "(").replace("}", ")")
    t = t.replace("#", "＃").replace(";", ",").replace("<br/>", " ")
    return t[:90]
