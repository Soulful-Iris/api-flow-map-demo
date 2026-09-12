#!/usr/bin/env python3
"""apiflow — map API request flows and review what a branch changes.

  scan     [repo] -o out/             Build the flow model + HTML/Markdown map of a repo
  diff     [repo] --base <ref> -o out/  Compare the working tree against a base branch (PR review)
  explain  <model|diff>.json           Compact outline for annotation (endpoints, step ids, changes)
  render   <model|diff>.json           Re-render HTML/Markdown, optionally merging an annotations file
  doctor   [repo]                      Check what the scanner will and will not understand

Exit codes: 0 ok · 1 usage/error · 2 completed with diagnostics · 3 git problem · 4 --fail-on-risk threshold hit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from apiflow import annotate, codediff, gitutil, render_html, render_md  # noqa: E402
from apiflow.differ import SEVERITY_ORDER, diff_models  # noqa: E402
from apiflow.discovery import Config  # noqa: E402
from apiflow.model import Model  # noqa: E402
from apiflow.scanner import scan  # noqa: E402


def _overrides(args: argparse.Namespace) -> Dict[str, Any]:
    ov: Dict[str, Any] = {}
    if getattr(args, "include", None):
        ov["include"] = args.include
    if getattr(args, "exclude", None):
        ov["exclude"] = args.exclude
    if getattr(args, "include_tests", False):
        ov["include_tests"] = True
    if getattr(args, "max_depth", None) is not None:
        ov["max_depth"] = args.max_depth
    if getattr(args, "no_snippets", False):
        ov["snippets"] = False
    return ov


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _outputs(out: str, stem: str, data: Dict[str, Any], mode: str, formats: str, quiet: bool = False) -> List[str]:
    written = []
    fmts = {"html", "md", "json"} if formats == "all" else set(formats.split(","))
    if "json" in fmts:
        p = os.path.join(out, f"{stem}.json")
        _write(p, json.dumps(data, indent=1, ensure_ascii=False))
        written.append(p)
    if "html" in fmts:
        p = os.path.join(out, f"{stem}.html")
        _write(p, render_html.render(data, mode))
        written.append(p)
    if "md" in fmts:
        p = os.path.join(out, f"{stem}.md")
        _write(p, render_md.render_diff(data) if mode == "diff" else render_md.render_scan(data))
        written.append(p)
    if not quiet:
        for p in written:
            print(f"wrote {p}")
    return written


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _mode_of(data: Dict[str, Any]) -> str:
    return "diff" if "summary" in data and "base" in data else "scan"


# ------------------------------------------------------------------------------ commands

def cmd_scan(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.repo)
    if not os.path.isdir(root):
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1
    model = scan(root, overrides=_overrides(args), include_spec=not args.no_spec)
    data = model.to_dict()
    if args.annotations:
        annotate.apply(data, annotate.load(args.annotations))
    _outputs(args.out, "flow-map", data, "scan", args.format, quiet=args.quiet)
    st = model.stats
    if not args.quiet:
        print(f"{st['endpoints']} endpoints, {st['steps']} steps, {st['files_scanned']} files, frameworks: {', '.join(model.frameworks) or 'none detected'} ({st['duration_ms']} ms)")
        for d in model.diagnostics[:10]:
            print(f"  ! {d}")
        if len(model.diagnostics) > 10:
            print(f"  ! … {len(model.diagnostics) - 10} more (see flow-map.json diagnostics)")
    if st["endpoints"] == 0:
        print("no endpoints found — run `apiflow.py doctor` to see what was detected", file=sys.stderr)
        return 2
    return 2 if model.diagnostics and args.strict else 0


def cmd_diff(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.repo)
    if not gitutil.is_repo(root):
        print(f"error: {root} is not a git repository (diff needs git; use `scan` on plain folders)", file=sys.stderr)
        return 3
    try:
        cfg = Config.load(root, _overrides(args))
        base_ref = gitutil.default_base(root, args.base or cfg.base_branch)
        head_ref = args.head
        if args.no_merge_base or head_ref:
            base_commit = base_ref if args.no_merge_base else gitutil.merge_base(root, base_ref, head_ref or "HEAD")
        else:
            base_commit = gitutil.merge_base(root, base_ref, "HEAD")
    except gitutil.GitError as exc:
        print(f"git error: {exc}", file=sys.stderr)
        return 3
    if not args.quiet:
        print(f"base: {base_ref} ({base_commit[:10]})  head: {head_ref or 'working tree'}")
    snap = None
    head_snap = None
    try:
        snap = gitutil.snapshot(root, base_commit)
        base_info = gitutil.describe(root, base_commit)
        base_info["ref"] = base_ref
        base_model = scan(snap, config=cfg, git_info=base_info, include_spec=not args.no_spec)
        if head_ref:
            head_snap = gitutil.snapshot(root, head_ref)
            head_info = gitutil.describe(root, head_ref)
            head_model = scan(head_snap, config=cfg, git_info=head_info, include_spec=not args.no_spec)
        else:
            head_info = gitutil.info(root)
            head_model = scan(root, config=cfg, git_info=head_info, include_spec=not args.no_spec)
    except gitutil.GitError as exc:
        print(f"git error: {exc}", file=sys.stderr)
        return 3
    finally:
        if snap:
            gitutil.cleanup(snap)
        if head_snap:
            gitutil.cleanup(head_snap)
    # keep the repo root pointing at the real checkout, not the temp snapshot
    head_model.repo["root"] = root
    head_model.repo["name"] = os.path.basename(root.rstrip("/")) or root
    data = diff_models(base_model, head_model, base_info=base_info, head_info=head_info)
    data["changed_files"] = gitutil.changed_files(root, base_commit, head_ref)
    if not args.no_code_diff:
        data["file_diffs"] = codediff.collect(root, base_commit, head_ref, codediff.files_for_diff(data))
    if args.annotations:
        annotate.apply(data, annotate.load(args.annotations))
    if args.with_models:
        _write(os.path.join(args.out, "flow-model-base.json"), json.dumps(base_model.to_dict(), indent=1))
        _write(os.path.join(args.out, "flow-model-head.json"), json.dumps(head_model.to_dict(), indent=1))
    _outputs(args.out, "flow-diff", data, "diff", args.format, quiet=args.quiet)
    s = data["summary"]
    if not args.quiet:
        print(f"endpoints: +{s['endpoints_added']} −{s['endpoints_removed']} ~{s['endpoints_modified']} ={s['endpoints_unchanged']} · "
              f"steps: +{s['steps_added']} −{s['steps_removed']} ~{s['steps_modified']} · risk: {s['risk']}")
        for r in s["risk_reasons"][:8]:
            print(f"  {r['severity']:6} {r['endpoint']}: {r['reason']}")
    if args.fail_on_risk and SEVERITY_ORDER.get(s["risk"], 0) >= SEVERITY_ORDER.get(args.fail_on_risk, 99):
        print(f"risk {s['risk']} reaches --fail-on-risk {args.fail_on_risk}", file=sys.stderr)
        return 4
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    data = _load_json(args.file)
    mode = _mode_of(data)
    out: List[str] = []
    eps = data.get("endpoints", [])
    if args.endpoint:
        eps = [e for e in eps if args.endpoint.lower() in e["id"].lower()]
    if mode == "diff" and args.changed_only:
        eps = [e for e in eps if e.get("status") != "unchanged"]
    if mode == "diff":
        s = data.get("summary", {})
        out.append(f"# diff {data.get('base', {}).get('ref', '')} -> {data.get('head', {}).get('branch', 'head')} · risk {s.get('risk')} · "
                   f"+{s.get('endpoints_added')} −{s.get('endpoints_removed')} ~{s.get('endpoints_modified')} endpoints")
    else:
        out.append(f"# scan · {len(eps)} endpoints · {', '.join(data.get('frameworks', []))}")
    for e in eps:
        head = f"## {e['id']}  [{e.get('status', 'scan')}] title=\"{e.get('title', '')}\""
        if e.get("risk") and e.get("risk") != "none":
            head += f" risk={e['risk']}"
        out.append(head)
        props = e.get("properties", {})
        keys = [k for k in ("auth", "validation", "body", "path_params", "query_params", "headers", "status", "responses", "returns", "feature_flags", "documented") if props.get(k) not in (None, [], "", False)]
        if keys:
            out.append("   props: " + "; ".join(f"{k}={props[k]}" for k in keys))
        for p in e.get("property_changes", []):
            out.append(f"   Δ {p['severity']}: {p['reason']}")
        for c in e.get("flow_changes", []):
            out.append(f"   Δ {c['severity']} {c['type']}: {c['reason']}" + (f"  (in {' › '.join(c['path'])})" if c.get("path") else "") + f"  id={c.get('step_id', '')}")
        if e.get("warnings"):
            for w in e["warnings"]:
                out.append(f"   ! {w}")
        _explain_flow(e.get("flow", []), out, 1, args.depth, mode)
    text = "\n".join(out) + "\n"
    if args.out:
        _write(args.out, text)
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(text)
    return 0


def _explain_flow(nodes: List[Dict[str, Any]], out: List[str], depth: int, max_depth: int, mode: str) -> None:
    if depth > max_depth:
        return
    for n in nodes:
        if n.get("kind") == "log" or n.get("change") == "moved-from":
            continue
        ch = n.get("change", "same")
        mark = {"added": "+", "removed": "-", "modified": "~", "moved": "^"}.get(ch, " ")
        meta = ""
        if n.get("outcome", {}).get("status"):
            meta += f" [{n['outcome']['status']}]"
        if n.get("outcome", {}).get("throws"):
            meta += f" throws {n['outcome']['throws']}"
        flags = [t for t in n.get("tags", []) if t.startswith("flag:") or t in ("early-exit", "unresolved", "recursion", "depth-limit", "interface-only")]
        if flags:
            meta += " {" + ",".join(flags) + "}"
        line = f"{mark}{'  ' * depth}{n.get('kind')}: {n.get('label')}{meta}  id={n.get('id', '')}"
        if n.get("detail") and n.get("kind") != "branch":
            line += f"  <{n['detail'][:90]}>"
        if ch == "modified" and n.get("before"):
            line += f"  was: {n['before'].get('label', '')}"
        out.append(line)
        if n.get("children") and "wrapper" not in n.get("tags", []):
            _explain_flow(n["children"], out, depth + 1, max_depth, mode)
        for i, b in enumerate(n.get("branches", [])):
            first_if = (i == 0 and n.get("kind") == "branch" and str(n.get("detail", "")).startswith("if ("))
            if not first_if:
                bm = {"added": "+", "removed": "-", "modified": "~"}.get(b.get("change", "same"), " ")
                out.append(f"{bm}{'  ' * (depth + 1)}◇ {b.get('label')}" + (" (exits)" if b.get("exits") else ""))
            _explain_flow(b.get("steps", []), out, depth + (1 if first_if else 2), max_depth, mode)


def cmd_render(args: argparse.Namespace) -> int:
    data = _load_json(args.file)
    mode = _mode_of(data)
    if args.annotations:
        annotate.apply(data, annotate.load(args.annotations))
    stem = args.stem or ("flow-diff" if mode == "diff" else "flow-map")
    _outputs(args.out, stem, data, mode, args.format, quiet=args.quiet)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.repo)
    from apiflow.discovery import Repo
    from apiflow.extractors import build_index, extract_all, extract_specs
    cfg = Config.load(root, _overrides(args))
    repo = Repo(root, cfg).load()
    by_lang: Dict[str, int] = {}
    for f in repo.files:
        by_lang[f.lang] = by_lang.get(f.lang, 0) + 1
    print(f"repo: {root}")
    print(f"git: {'yes' if gitutil.is_repo(root) else 'no (scan works, diff needs git)'}")
    print(f"files considered: {len(repo.files)}  " + ", ".join(f"{k}={v}" for k, v in sorted(by_lang.items(), key=lambda x: -x[1])))
    index = build_index(repo)
    seeds = extract_all(repo, index)
    specs = extract_specs(repo)
    fw = sorted(index.frameworks.keys())
    print(f"frameworks detected: {', '.join(fw) or 'none'}")
    print(f"endpoints found in code: {len(seeds)}   declared in OpenAPI: {len(specs)}")
    untraced = [s for s in seeds if s.fn is None]
    if untraced:
        print(f"endpoints whose handler could not be located ({len(untraced)}):")
        for s in untraced[:15]:
            print(f"  - {s.endpoint.method} {s.endpoint.path}  ({s.endpoint.handler.name} in {s.endpoint.handler.file})")
    try:
        import yaml  # noqa: F401
        print("PyYAML: available (YAML specs / serverless.yml are read)")
    except Exception:
        print("PyYAML: missing — YAML OpenAPI specs and serverless.yml will be skipped (pip install pyyaml)")
    if repo.diagnostics:
        print("diagnostics:")
        for d in repo.diagnostics[:20]:
            print(f"  ! {d}")
    if not seeds and not specs:
        print(textwrap.dedent("""
            No endpoints found. Common causes:
              - the API lives in a subfolder not covered by `include` (see .apiflow.json)
              - routes are registered through a pattern the scanner does not know (see references/frameworks.md)
              - files are larger than max_file_kb or excluded by .gitignore"""))
        return 2
    return 0


# ------------------------------------------------------------------------------ main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="apiflow.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--include", action="append", help="glob of paths to include (repeatable)")
        sp.add_argument("--exclude", action="append", help="glob of paths to exclude (repeatable)")
        sp.add_argument("--include-tests", action="store_true", help="also scan test directories")
        sp.add_argument("--max-depth", type=int, help="how deep to follow internal calls (default 4)")
        sp.add_argument("--no-snippets", action="store_true", help="omit code snippets from the output")
        sp.add_argument("--no-spec", action="store_true", help="ignore OpenAPI/Swagger files")
        sp.add_argument("-q", "--quiet", action="store_true")

    sp = sub.add_parser("scan", help="map every endpoint of a repository")
    sp.add_argument("repo", nargs="?", default=".")
    sp.add_argument("-o", "--out", default="apiflow-out")
    sp.add_argument("--format", default="all", help="all | html,md,json (comma separated)")
    sp.add_argument("--annotations", help="JSON with titles/summaries to merge (see SKILL.md)")
    sp.add_argument("--strict", action="store_true", help="exit 2 when the scan produced diagnostics")
    common(sp)
    sp.set_defaults(fn=cmd_scan)

    sp = sub.add_parser("diff", help="compare the working tree (or --head) against a base branch")
    sp.add_argument("repo", nargs="?", default=".")
    sp.add_argument("--base", help="base ref (default: origin/HEAD, main, master, develop)")
    sp.add_argument("--head", help="compare a committed ref instead of the working tree")
    sp.add_argument("--no-merge-base", action="store_true", help="diff against the base tip instead of merge-base(base, head)")
    sp.add_argument("-o", "--out", default="apiflow-out")
    sp.add_argument("--format", default="all")
    sp.add_argument("--annotations")
    sp.add_argument("--with-models", action="store_true", help="also write the base/head flow models")
    sp.add_argument("--fail-on-risk", choices=["low", "medium", "high"], help="exit 4 when the overall risk reaches this level (CI gate)")
    sp.add_argument("--no-code-diff", action="store_true", help="do not embed source diffs of the touched files (smaller output)")
    common(sp)
    sp.set_defaults(fn=cmd_diff)

    sp = sub.add_parser("explain", help="print a compact outline with step ids, for writing annotations")
    sp.add_argument("file")
    sp.add_argument("--endpoint", help="only endpoints whose id contains this text")
    sp.add_argument("--changed-only", action="store_true", help="diff files: skip unchanged endpoints")
    sp.add_argument("--depth", type=int, default=4)
    sp.add_argument("-o", "--out")
    sp.set_defaults(fn=cmd_explain)

    sp = sub.add_parser("render", help="re-render a model or diff JSON, merging annotations")
    sp.add_argument("file")
    sp.add_argument("--annotations")
    sp.add_argument("-o", "--out", default="apiflow-out")
    sp.add_argument("--format", default="all")
    sp.add_argument("--stem", help="output file stem (default flow-map / flow-diff)")
    sp.add_argument("-q", "--quiet", action="store_true")
    sp.set_defaults(fn=cmd_render)

    sp = sub.add_parser("doctor", help="report what the scanner detects in a repository")
    sp.add_argument("repo", nargs="?", default=".")
    common(sp)
    sp.set_defaults(fn=cmd_doctor)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return 130
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
