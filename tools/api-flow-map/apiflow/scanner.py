"""scan(root) -> Model: the single entry point used by the CLI and the differ."""
from __future__ import annotations

import datetime as _dt
import os
import time
from typing import Dict, List, Optional

from . import gitutil, labeler
from .discovery import Config, Repo
from .extractors import build_index, extract_all, extract_specs
from .model import Endpoint, Model, count_steps, finalize_ids, normalize_path
from .tracer import Tracer


def scan(root: str, config: Optional[Config] = None, overrides: Optional[Dict] = None, git_info: Optional[Dict] = None,
         include_spec: bool = True) -> Model:
    t0 = time.time()
    root = os.path.abspath(root)
    cfg = config or Config.load(root, overrides)
    repo = Repo(root, cfg).load()
    index = build_index(repo)
    seeds = extract_all(repo, index)
    tracer = Tracer(index, cfg)

    endpoints: List[Endpoint] = []
    seen: Dict[str, Endpoint] = {}
    for seed in seeds:
        ep = seed.endpoint
        ep.path = normalize_path(ep.path)
        ep.id = ep.compute_id()
        if ep.id in seen:
            prev = seen[ep.id]
            # same route registered twice (e.g. overloads / viewset + explicit): keep the first, note the duplicate
            if seed.fn is not None and not prev.flow and prev.handler.name != ep.handler.name:
                pass
            if ep.handler.name != prev.handler.name:
                prev.warnings.append(f"also registered by {ep.handler.name} ({ep.handler.file}:{ep.handler.line})")
            continue
        ep.flow = tracer.trace(seed)
        ep.auto_title = labeler.endpoint_title(ep.method, ep.path, ep.handler.name, ep.properties.get("spec_summary", ""), cfg.endpoint_titles)
        ep.title = ep.auto_title
        for s in _all_steps(ep.flow):
            s.auto_label = s.label
        finalize_ids(ep)
        seen[ep.id] = ep
        endpoints.append(ep)

    if include_spec:
        for spec_ep in extract_specs(repo):
            spec_ep.path = normalize_path(spec_ep.path)
            spec_ep.id = spec_ep.compute_id()
            code_ep = seen.get(spec_ep.id)
            if code_ep is not None:
                code_ep.properties["documented"] = True
                if spec_ep.properties.get("spec_summary") and not code_ep.properties.get("spec_summary"):
                    code_ep.properties["spec_summary"] = spec_ep.properties["spec_summary"]
                    code_ep.auto_title = labeler.endpoint_title(code_ep.method, code_ep.path, code_ep.handler.name, spec_ep.properties["spec_summary"], cfg.endpoint_titles)
                    code_ep.title = code_ep.auto_title
                if spec_ep.properties.get("declared_responses"):
                    code_ep.properties.setdefault("declared_responses", spec_ep.properties["declared_responses"])
                if spec_ep.properties.get("auth") and not code_ep.properties.get("auth"):
                    code_ep.properties["spec_auth"] = spec_ep.properties["auth"]
                continue
            spec_ep.auto_title = spec_ep.title or labeler.endpoint_title(spec_ep.method, spec_ep.path, "", spec_ep.properties.get("spec_summary", ""))
            spec_ep.title = spec_ep.auto_title
            spec_ep.warnings.append("declared in the OpenAPI spec but no implementation was found in the code")
            finalize_ids(spec_ep)
            seen[spec_ep.id] = spec_ep
            endpoints.append(spec_ep)
        spec_seen = any(e.source == "openapi" or e.properties.get("documented") for e in endpoints)
        if spec_seen:
            for ep in endpoints:
                if ep.source == "code" and "documented" not in ep.properties:
                    ep.properties["documented"] = False

    endpoints.sort(key=lambda e: (e.path.lower(), _method_order(e.method)))
    model = Model()
    model.generated_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    model.repo = {"root": root, "name": os.path.basename(root.rstrip("/")) or root}
    model.repo["git"] = git_info if git_info is not None else gitutil.info(root)
    model.frameworks = sorted(index.frameworks.keys())
    model.stats = {
        "files_scanned": len(repo.files),
        "functions_indexed": len(index.functions),
        "endpoints": len(endpoints),
        "steps": sum(count_steps(e.flow) for e in endpoints),
        "untraced_endpoints": sum(1 for e in endpoints if not e.flow and e.source == "code"),
        "duration_ms": int((time.time() - t0) * 1000),
    }
    model.diagnostics = list(repo.diagnostics)
    model.endpoints = endpoints
    return model


def _all_steps(steps):
    for s in steps:
        yield s
        yield from _all_steps(s.children)
        for b in s.branches:
            yield from _all_steps(b.steps)


def _method_order(m: str) -> int:
    return {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}.get(m.upper(), 9)
