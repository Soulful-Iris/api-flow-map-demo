"""OpenAPI / Swagger specs: the contract the code is supposed to honour.

Endpoints found here are reconciled with the code by (method, path):
- documented + implemented   -> the code endpoint gets `documented: true` and the spec summary
- documented, no code found  -> an endpoint with source "openapi" and a warning
- implemented, no spec entry -> the code endpoint gets `documented: false`
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from ..model import Endpoint, Handler, join_paths

VERBS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")


def _load(sf) -> Any:
    if sf.path.lower().endswith(".json"):
        try:
            return json.loads(sf.text)
        except Exception:
            return None
    try:
        import yaml  # type: ignore
    except Exception:
        return "no-yaml"
    try:
        return yaml.safe_load(sf.text)
    except Exception:
        return None


def extract(files, diagnostics: List[str]) -> List[Endpoint]:
    out: List[Endpoint] = []
    for sf in files:
        data = _load(sf)
        if data == "no-yaml":
            diagnostics.append(f"{sf.path}: PyYAML is not installed, so this YAML spec was not read (pip install pyyaml)")
            continue
        if not isinstance(data, dict) or not isinstance(data.get("paths"), dict):
            continue
        if not (data.get("openapi") or data.get("swagger")):
            continue
        base = ""
        if isinstance(data.get("basePath"), str):
            base = data["basePath"]
        elif isinstance(data.get("servers"), list) and data["servers"]:
            url = str(data["servers"][0].get("url", ""))
            m = re.match(r"^(?:https?://[^/]+)?(/.*)?$", url)
            if m and m.group(1) and m.group(1) != "/":
                base = m.group(1)
        global_security = data.get("security")
        for path, item in data["paths"].items():
            if not isinstance(item, dict):
                continue
            common_params = item.get("parameters", []) or []
            for verb in VERBS:
                op = item.get(verb)
                if not isinstance(op, dict):
                    continue
                props: Dict[str, Any] = {"documented": True}
                summary = op.get("summary") or op.get("description") or ""
                if summary:
                    props["spec_summary"] = str(summary).strip().split("\n")[0][:160]
                if op.get("operationId"):
                    props["operation_id"] = op["operationId"]
                params = list(common_params) + list(op.get("parameters", []) or [])
                for p in params:
                    if not isinstance(p, dict):
                        continue
                    loc, name = p.get("in"), p.get("name")
                    if not name:
                        continue
                    if loc == "path":
                        props.setdefault("path_params", []).append(name)
                    elif loc == "query":
                        props.setdefault("query_params", []).append(name + ("" if p.get("required") else "?"))
                    elif loc == "header":
                        props.setdefault("headers", []).append(name)
                    elif loc == "body":
                        props["body"] = _schema_name(p.get("schema"))
                rb = op.get("requestBody")
                if isinstance(rb, dict):
                    content = rb.get("content", {}) or {}
                    for ctype, spec in content.items():
                        props["body"] = _schema_name((spec or {}).get("schema")) or "body"
                        props.setdefault("consumes", []).append(ctype)
                        break
                responses = op.get("responses", {}) or {}
                codes = []
                for c in responses:
                    try:
                        codes.append(int(c))
                    except (TypeError, ValueError):
                        pass
                if codes:
                    props["declared_responses"] = sorted(codes)
                sec = op.get("security", global_security)
                if sec:
                    schemes = []
                    for s in sec if isinstance(sec, list) else []:
                        if isinstance(s, dict):
                            for k, v in s.items():
                                schemes.append(k + (f"[{', '.join(map(str, v))}]" if v else ""))
                    props["auth"] = schemes or ["required"]
                elif sec == []:
                    props["auth"] = ["public"]
                if op.get("deprecated"):
                    props["deprecated"] = True
                if op.get("tags"):
                    props["tags"] = [str(t) for t in op["tags"]]
                ep = Endpoint(method=verb.upper(), path=join_paths(base, str(path)), handler=Handler(op.get("operationId", "") or "", sf.path, 0, "openapi"),
                              framework="openapi", source="openapi", properties=props)
                ep.title = str(summary).strip().split("\n")[0][:120] if summary else ""
                out.append(ep)
    return out


def _schema_name(schema) -> str:
    if not isinstance(schema, dict):
        return ""
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.split("/")[-1]
    items = schema.get("items")
    if isinstance(items, dict) and items.get("$ref"):
        return items["$ref"].split("/")[-1] + "[]"
    return schema.get("title") or schema.get("type") or ""
