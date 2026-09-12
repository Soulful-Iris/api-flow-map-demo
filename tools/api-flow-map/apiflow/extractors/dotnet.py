"""Endpoints for C# ASP.NET Core: attribute routing on controllers plus minimal APIs."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .. import lexer
from ..httpcodes import status_from_expr
from ..model import Endpoint, Handler, join_paths
from .annotations import as_list, find_annotations, parse_args
from .base import CodeIndex, EndpointSeed, FunctionDef, parse_params

HTTP_ATTRS = {"HttpGet": "GET", "HttpPost": "POST", "HttpPut": "PUT", "HttpPatch": "PATCH", "HttpDelete": "DELETE",
              "HttpHead": "HEAD", "HttpOptions": "OPTIONS"}
_MAP_RX = re.compile(r"\b([A-Za-z_]\w*)\.(MapGet|MapPost|MapPut|MapPatch|MapDelete|MapMethods|Map)\s*\(")
_GROUP_RX = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\.MapGroup\(\s*\"([^\"]*)\"")


def _controller_token(class_name: str) -> str:
    return re.sub(r"Controller$", "", class_name)


def extract(index: CodeIndex, files) -> List[EndpointSeed]:
    seeds: List[EndpointSeed] = []
    for sf in files:
        blocks = index.blocks.get(sf.path, [])
        for b in blocks:
            if b.kind == "class" and (any(e in ("ControllerBase", "Controller", "ApiController") or e.endswith("Controller") for e in b.extends)
                                      or "ApiController" in b.sig or "[Route" in b.sig):
                seeds.extend(_controller(index, sf, b, blocks))
        if _MAP_RX.search(sf.masked):
            seeds.extend(_minimal(index, sf))
    return seeds


def _controller(index: CodeIndex, sf, b, blocks) -> List[EndpointSeed]:
    seeds: List[EndpointSeed] = []
    cattrs = find_annotations(b.sig, style="bracket")
    bases: List[str] = [""]
    class_auth: List[str] = []
    area = ""
    for n, a in cattrs:
        pa = parse_args(a)
        if n == "Route":
            bases = [str(x) for x in as_list(pa.get("value"))] or [""]
        elif n == "Authorize":
            class_auth.append("Authorize" + (f"({a.strip()})" if a.strip() else ""))
        elif n == "AllowAnonymous":
            class_auth.append("AllowAnonymous")
        elif n == "Area":
            area = str(pa.get("value", ""))
    cls = index.class_named(b.name, sf.path)
    token = _controller_token(b.name)
    index.note_framework("aspnet-core")
    for fb in blocks:
        if fb.kind != "function" or fb.parent is not b:
            continue
        attrs = find_annotations(fb.sig, style="bracket")
        verbs: List[str] = []
        paths: List[str] = [""]
        props: Dict = {}
        auth = list(class_auth)
        for n, a in attrs:
            pa = parse_args(a)
            if n in HTTP_ATTRS:
                verbs.append(HTTP_ATTRS[n])
                v = pa.get("value")
                if isinstance(v, str):
                    paths = [v]
            elif n == "Route":
                paths = [str(x) for x in as_list(pa.get("value"))] or paths
            elif n == "AcceptVerbs":
                verbs.extend(str(x).upper() for x in as_list(pa.get("value")))
            elif n == "Authorize":
                auth.append("Authorize" + (f"({a.strip()})" if a.strip() else ""))
            elif n == "AllowAnonymous":
                auth.append("AllowAnonymous")
            elif n == "ProducesResponseType":
                code = status_from_expr(a)
                if code:
                    props.setdefault("declared_responses", []).append(code)
            elif n == "Obsolete":
                props["deprecated"] = True
            elif n in ("ResponseCache", "OutputCache"):
                props.setdefault("cache", []).append(n)
            elif n in ("EnableRateLimiting", "RateLimit"):
                props.setdefault("rate_limit", []).append(n + (f"({a.strip()})" if a.strip() else ""))
            elif n in ("Consumes", "Produces"):
                props[n.lower()] = [str(x) for x in as_list(pa.get("value"))]
        if not verbs:
            continue
        path_params, query, headers, body = [], [], [], ""
        for ann_txt, ptype, pname in parse_params("csharp", fb.params):
            pan = find_annotations(ann_txt, style="bracket")
            names = {n for n, _ in pan}
            short = re.sub(r"<.*", "", ptype).split(".")[-1].rstrip("?")
            if "FromBody" in names:
                body = short
            elif "FromQuery" in names:
                query.append(pname)
            elif "FromRoute" in names:
                path_params.append(pname)
            elif "FromHeader" in names:
                headers.append(pname)
            elif "FromServices" in names or "FromKeyedServices" in names:
                continue
            elif short and short[0].isupper() and short not in ("String", "Guid", "Int32", "Int64", "Boolean", "CancellationToken", "DateTime", "Decimal", "Double") and short not in ("int", "long", "string"):
                body = body or short
        for p in paths:
            for m in re.finditer(r"\{(\w+)[^}]*\}", p):
                if m.group(1) not in path_params:
                    path_params.append(m.group(1))
        for ann_txt, ptype, pname in parse_params("csharp", fb.params):
            if ptype.rstrip("?") in ("int", "long", "string", "Guid", "bool", "decimal", "double") and pname not in path_params and pname not in query and not find_annotations(ann_txt, style="bracket"):
                query.append(pname)
        if path_params:
            props["path_params"] = path_params
        if query:
            props["query_params"] = query
        if headers:
            props["headers"] = headers
        if body:
            props["body"] = body
        if auth:
            props["auth"] = auth
        if "[ApiController]" in b.sig or "ApiController" in b.sig:
            props.setdefault("validation", []).append("[ApiController] model state")
        if "Task<" in fb.sig or "async " in fb.sig:
            props["async"] = True
        rm = re.search(r"(?:Task<)?(?:ActionResult<)?([A-Za-z_][\w<>]*?)>?\s+" + re.escape(fb.name) + r"\s*\(", fb.sig)
        if rm:
            props["returns"] = rm.group(1)
        fn = cls.methods.get(fb.name) if cls else None
        for base in bases:
            for p in paths:
                full = join_paths(base, p).replace("[controller]", token.lower()).replace("[action]", fb.name.lower()).replace("[area]", area.lower())
                for verb in verbs:
                    ep = Endpoint(method=verb, path=full, handler=Handler(f"{b.name}.{fb.name}", sf.path, fb.line, "csharp"),
                                  framework="aspnet-core", properties=dict(props))
                    seeds.append(EndpointSeed(endpoint=ep, fn=fn, owner_class=cls))
    return seeds


def _minimal(index: CodeIndex, sf) -> List[EndpointSeed]:
    seeds: List[EndpointSeed] = []
    masked = sf.masked
    group_prefix: Dict[str, str] = {}
    for m in _GROUP_RX.finditer(sf.text):
        group_prefix[m.group(1)] = join_paths(group_prefix.get(m.group(2), ""), m.group(3))
    for m in _MAP_RX.finditer(masked):
        recv, kind = m.group(1), m.group(2)
        open_idx = m.end() - 1
        close = lexer.match_bracket(masked, open_idx)
        if close < 0:
            continue
        args = lexer.split_top_level(sf.text[open_idx + 1:close], ",")
        if len(args) < 2:
            continue
        path = lexer.strip_string_quotes(args[0])
        if kind == "MapMethods":
            verbs = re.findall(r"\"(\w+)\"|HttpMethods\.(\w+)", args[1])
            verbs = [(a or b).upper() for a, b in verbs] or ["ANY"]
            handler = args[-1]
        elif kind == "Map":
            verbs, handler = ["ANY"], args[-1]
        else:
            verbs, handler = [kind[3:].upper()], args[-1]
        chain = sf.text[close + 1:close + 300]
        props: Dict = {}
        if "RequireAuthorization" in chain:
            am = re.search(r"RequireAuthorization\(([^)]*)\)", chain)
            props["auth"] = ["RequireAuthorization" + (f"({am.group(1)})" if am and am.group(1) else "")]
        if "AllowAnonymous" in chain:
            props["auth"] = ["AllowAnonymous"]
        if "RequireRateLimiting" in chain:
            props["rate_limit"] = ["RequireRateLimiting"]
        if "CacheOutput" in chain:
            props["cache"] = ["CacheOutput"]
        pp = re.findall(r"\{(\w+)[^}]*\}", path)
        if pp:
            props["path_params"] = pp
        fn: Optional[FunctionDef] = None
        h = handler.strip()
        if "=>" in h:
            seg = masked[open_idx:close]
            hm = re.search(r"=>\s*\{", seg)
            if hm:
                brace = open_idx + hm.end() - 1
                bclose = lexer.match_bracket(masked, brace)
                if bclose > 0:
                    fn = FunctionDef(name=f"{verbs[0].lower()} {path}", file=sf, start=brace + 1, end=bclose, line=sf.lines.line(m.start()), params=re.sub(r"=>.*", "", h, flags=re.S), sig="lambda")
            else:
                hm2 = re.search(r"=>", seg)
                if hm2:
                    fn = FunctionDef(name=f"{verbs[0].lower()} {path}", file=sf, start=open_idx + hm2.end(), end=close, line=sf.lines.line(m.start()), params="", sig="lambda")
            hname = "inline lambda"
        else:
            parts = h.split(".")
            fn = index.method_of(parts[0], parts[-1], sf.path) if len(parts) > 1 else index.unique_function(parts[0], sf.path)
            hname = h
        index.note_framework("aspnet-minimal")
        for verb in verbs:
            ep = Endpoint(method=verb, path=join_paths(group_prefix.get(recv, ""), path), handler=Handler(hname, sf.path, sf.lines.line(m.start()), "csharp"),
                          framework="aspnet-minimal", properties=dict(props))
            if fn is None:
                ep.warnings.append(f"handler `{h[:40]}` could not be located; flow not traced")
            seeds.append(EndpointSeed(endpoint=ep, fn=fn))
    return seeds
