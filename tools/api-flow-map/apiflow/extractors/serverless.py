"""API Gateway routes declared in serverless.yml or an AWS SAM template, mapped
to the Lambda handler function in the repo so the flow can be traced."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from ..model import Endpoint, Handler, join_paths
from .base import CodeIndex, EndpointSeed, FunctionDef


def _load_yaml(sf, diagnostics: List[str]) -> Optional[Dict]:
    try:
        import yaml  # type: ignore
    except Exception:
        diagnostics.append(f"{sf.path}: PyYAML is not installed, so serverless/SAM routes were not read (pip install pyyaml)")
        return None

    class _Loader(yaml.SafeLoader):
        pass

    def _any(loader, tag_suffix, node):
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_mapping(node)

    _Loader.add_multi_constructor("!", _any)
    try:
        return yaml.load(sf.text, Loader=_Loader)
    except Exception as exc:
        diagnostics.append(f"{sf.path}: could not parse YAML ({exc})")
        return None


def _find_handler(index: CodeIndex, handler: str, code_uri: str = "") -> Optional[FunctionDef]:
    """`src/handlers/orders.getOrder` -> function getOrder in src/handlers/orders.(js|ts|py)."""
    if not handler:
        return None
    mod, _, fn_name = handler.rpartition(".")
    if not fn_name:
        return None
    bases = [mod, os.path.join(code_uri, mod).replace(os.sep, "/") if code_uri else mod]
    bases += [b.replace(".", "/") for b in list(bases)]
    for base in bases:
        base = base.strip("./")
        for ext in (".js", ".ts", ".mjs", ".cjs", ".py", "/index.js", "/index.ts", "/__init__.py"):
            p = base + ext
            if p in index.repo.by_path:
                fn = index.function_in_file(p, fn_name)
                if fn:
                    return fn
                local = index.exports.get(p, {}).get(fn_name)
                if local:
                    fn = index.function_in_file(p, local)
                    if fn:
                        return fn
    # Java: com.acme.OrderHandler::handleRequest or com.acme.OrderHandler
    jm = re.match(r"^([\w.]+)(?:::(\w+))?$", handler)
    if jm:
        cls = jm.group(1).split(".")[-1]
        fn = index.method_of(cls, jm.group(2) or "handleRequest")
        if fn:
            return fn
    return index.unique_function(fn_name)


def extract(index: CodeIndex, files, diagnostics: List[str]) -> List[EndpointSeed]:
    seeds: List[EndpointSeed] = []
    for sf in files:
        data = _load_yaml(sf, diagnostics)
        if not isinstance(data, dict):
            continue
        base = os.path.basename(sf.path).lower()
        if base.startswith("serverless") and isinstance(data.get("functions"), dict):
            index.note_framework("serverless")
            for fname, spec in data["functions"].items():
                if not isinstance(spec, dict):
                    continue
                handler = str(spec.get("handler", ""))
                fn = _find_handler(index, handler)
                for ev in spec.get("events", []) or []:
                    if not isinstance(ev, dict):
                        continue
                    for key in ("http", "httpApi", "alb"):
                        e = ev.get(key)
                        if e is None:
                            continue
                        method, path = "ANY", "/"
                        if isinstance(e, str):
                            parts = e.split()
                            if len(parts) == 2:
                                method, path = parts
                            elif len(parts) == 1:
                                path = parts[0]
                        elif isinstance(e, dict):
                            method = str(e.get("method", "ANY")).upper()
                            path = str(e.get("path", "/"))
                        props: Dict[str, Any] = {"lambda": fname}
                        if isinstance(e, dict):
                            auth = e.get("authorizer")
                            if auth:
                                props["auth"] = [auth if isinstance(auth, str) else str(auth.get("name") or auth.get("type") or auth.get("arn") or "authorizer")]
                            if e.get("private"):
                                props["auth"] = props.get("auth", []) + ["api key"]
                        if spec.get("timeout"):
                            props["timeout"] = spec["timeout"]
                        ep = Endpoint(method=method.upper(), path=path, handler=Handler(handler, fn.file.path if fn else sf.path, fn.line if fn else 0, fn.lang if fn else "yaml"),
                                      framework="serverless", source="serverless", properties=props)
                        if fn is None:
                            ep.warnings.append(f"handler `{handler}` not found in the repo")
                        seeds.append(EndpointSeed(endpoint=ep, fn=fn))
        elif isinstance(data.get("Resources"), dict):
            resources = data["Resources"]
            globals_ = (data.get("Globals") or {}).get("Function", {}) if isinstance(data.get("Globals"), dict) else {}
            for rname, res in resources.items():
                if not isinstance(res, dict) or res.get("Type") != "AWS::Serverless::Function":
                    continue
                index.note_framework("aws-sam")
                p = res.get("Properties", {}) or {}
                handler = str(p.get("Handler") or globals_.get("Handler") or "")
                code_uri = str(p.get("CodeUri") or globals_.get("CodeUri") or "")
                fn = _find_handler(index, handler, code_uri)
                for ename, ev in (p.get("Events", {}) or {}).items():
                    if not isinstance(ev, dict) or ev.get("Type") not in ("Api", "HttpApi"):
                        continue
                    ep_props = ev.get("Properties", {}) or {}
                    path = str(ep_props.get("Path", "/"))
                    method = str(ep_props.get("Method", "ANY")).upper()
                    props = {"lambda": rname}
                    auth = ep_props.get("Auth")
                    if isinstance(auth, dict) and auth.get("Authorizer"):
                        props["auth"] = [str(auth["Authorizer"])]
                    if p.get("Timeout"):
                        props["timeout"] = p["Timeout"]
                    ep = Endpoint(method=method, path=path, handler=Handler(handler, fn.file.path if fn else sf.path, fn.line if fn else 0, fn.lang if fn else "yaml"),
                                  framework="aws-sam", source="serverless", properties=props)
                    if fn is None:
                        ep.warnings.append(f"handler `{handler}` not found in the repo")
                    seeds.append(EndpointSeed(endpoint=ep, fn=fn))
    return seeds
