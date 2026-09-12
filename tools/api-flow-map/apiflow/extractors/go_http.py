"""Endpoints for Go: net/http (incl. 1.22 method patterns), gin, echo, chi, gorilla/mux."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .. import lexer
from ..model import Endpoint, Handler, join_paths
from .base import CodeIndex, EndpointSeed, FunctionDef
from .js_ts import AUTH_RX, CACHE_RX, RATE_RX, VALIDATE_RX

VERB_METHODS = {"GET": "GET", "POST": "POST", "PUT": "PUT", "PATCH": "PATCH", "DELETE": "DELETE", "HEAD": "HEAD", "OPTIONS": "OPTIONS",
                "Any": "ANY", "Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE", "Head": "HEAD",
                "Options": "OPTIONS", "Handle": "ANY", "HandleFunc": "ANY", "Method": "", "MethodFunc": "", "Any_": "ANY"}
_ROUTE_RX = re.compile(r"\b([A-Za-z_]\w*)(?:\.With\((?:[^()]|\([^()]*\))*\))?\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Any|Get|Post|Put|Patch|Delete|Head|Options|Handle|HandleFunc|Method|MethodFunc)\s*\(")
_GROUP_RX = re.compile(r"\b([A-Za-z_]\w*)\s*:?=\s*([A-Za-z_]\w*)\.(Group|PathPrefix)\(\s*\"")
_ROUTE_FN_RX = re.compile(r"\b([A-Za-z_]\w*)\.(Route|Group)\(\s*(?:\"([^\"]*)\"\s*,\s*)?func\s*\(\s*([A-Za-z_]\w*)\s+[\w.]*Router\s*\)\s*\{")
_MOUNT_RX = re.compile(r"\b([A-Za-z_]\w*)\.Mount\(\s*\"([^\"]*)\"\s*,\s*([A-Za-z_][\w.]*)\s*\(")
_USE_RX = re.compile(r"\b([A-Za-z_]\w*)\.Use\(")
_METHODS_CHAIN_RX = re.compile(r"\.Methods\(([^)]*)\)")
_HTTP_VERB_CONST = re.compile(r"http\.Method(\w+)")


def _classify_mw(name: str) -> str:
    if AUTH_RX.search(name):
        return "auth"
    if VALIDATE_RX.search(name):
        return "validation"
    if RATE_RX.search(name):
        return "rate_limit"
    if CACHE_RX.search(name):
        return "cache"
    return "middleware"


def _framework(sf) -> str:
    t = sf.text
    if "gin-gonic/gin" in t:
        return "gin"
    if "labstack/echo" in t:
        return "echo"
    if "go-chi/chi" in t:
        return "chi"
    if "gorilla/mux" in t:
        return "gorilla-mux"
    if "gofiber/fiber" in t:
        return "fiber"
    return "net/http"


def _string_arg(a: str) -> Optional[str]:
    a = a.strip()
    if len(a) >= 2 and a[0] in "\"`" and a[-1] == a[0]:
        return a[1:-1]
    return None


def _local_types(sf, start: int, end: int, fn_params: str) -> Dict[str, str]:
    hints: Dict[str, str] = dict(getattr(sf, "_go_hints", {}))
    for m in re.finditer(r"([A-Za-z_]\w*)\s+\*?(?:[\w]+\.)?([A-Z]\w*)", fn_params):
        hints[m.group(1)] = m.group(2)
    body = sf.text[start:end]
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*:?=\s*(?:&\s*)?(?:[\w]+\.)?([A-Z]\w*)\{", body):
        hints[m.group(1)] = m.group(2)
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(?:,\s*\w+\s*)?:?=\s*(?:[\w]+\.)?New([A-Z]\w*)\(", body):
        hints[m.group(1)] = m.group(2)
    return hints


def _resolve(index: CodeIndex, sf, expr: str, hints: Dict[str, str], package: str) -> Tuple[Optional[FunctionDef], str]:
    e = expr.strip()
    e = re.sub(r"^http\.HandlerFunc\((.*)\)$", r"\1", e)
    # middleware wrappers: auth(h.Get) -> h.Get
    m = re.match(r"^([A-Za-z_][\w.]*)\((.*)\)$", e, re.S)
    if m and not e.startswith("func"):
        inner = lexer.split_top_level(m.group(2), ",")
        if inner:
            return _resolve(index, sf, inner[-1], hints, package)
    parts = e.split(".")
    if len(parts) == 1:
        fn = index.function_in_file(sf.path, parts[0])
        if fn:
            return fn, parts[0]
        for f in index.go_packages.get(package, []):
            if f.name == parts[0] and f.has_body:
                return f, parts[0]
        return index.unique_function(parts[0], sf.path), parts[0]
    recv, meth = parts[-2], parts[-1]
    t = hints.get(recv)
    if t:
        fn = index.method_of(t, meth, sf.path)
        if fn:
            return fn, f"{t}.{meth}"
    for f in index.go_packages.get(recv, []):
        if f.name == meth and f.has_body:
            return f, f"{recv}.{meth}"
    fn = index.method_of(recv, meth, sf.path)
    if fn:
        return fn, f"{recv}.{meth}"
    return index.unique_function(meth, sf.path), e


def _inline_fn(sf, arg_text: str, abs_start: int, name: str) -> Optional[FunctionDef]:
    seg = sf.masked[abs_start:abs_start + len(arg_text)]
    m = re.search(r"\bfunc\s*\(([^)]*)\)[^{]*\{", seg)
    if not m:
        return None
    brace = abs_start + m.end() - 1
    close = lexer.match_bracket(sf.masked, brace)
    if close < 0:
        return None
    return FunctionDef(name=name, file=sf, start=brace + 1, end=close, line=sf.lines.line(abs_start), params=m.group(1), sig="func")


def extract(index: CodeIndex, files) -> List[EndpointSeed]:
    seeds: List[EndpointSeed] = []
    for sf in files:
        sf._go_hints = index.module_vars.get(sf.path, {})
        blocks = index.blocks.get(sf.path, [])
        for b in blocks:
            if b.kind != "function":
                continue
            if not _ROUTE_RX.search(sf.masked, b.open, b.close) and not _MOUNT_RX.search(sf.masked, b.open, b.close):
                continue
            hints = _local_types(sf, b.open, b.close, b.params)
            _scan_scope(index, sf, b.open + 1, b.close, {}, {}, hints, seeds, set(), b.name)
    return seeds


def _scan_scope(index: CodeIndex, sf, start: int, end: int, var_prefix: Dict[str, str], var_mw: Dict[str, List[str]],
                hints: Dict[str, str], seeds: List[EndpointSeed], visited: set, scope_name: str) -> None:
    masked = sf.masked
    package = ""
    for f in index.functions:
        if f.file is sf:
            package = f.package
            break
    fw = _framework(sf)
    i = start
    events: List[Tuple[int, str, re.Match]] = []
    for rx, kind in ((_GROUP_RX, "group"), (_ROUTE_FN_RX, "routefn"), (_MOUNT_RX, "mount"), (_USE_RX, "use"), (_ROUTE_RX, "route")):
        for m in rx.finditer(masked, start, end):
            events.append((m.start(), kind, m))
    events.sort(key=lambda e: e[0])
    skip_until = -1
    for pos, kind, m in events:
        if pos < skip_until:
            continue
        if kind == "group":
            new, base, _ = m.group(1), m.group(2), m.group(3)
            q = masked.find('"', m.end() - 1)
            close_q = masked.find('"', q + 1)
            path = sf.text[q + 1:close_q] if q >= 0 and close_q > q else ""
            var_prefix[new] = join_paths(var_prefix.get(base, ""), path) if path else var_prefix.get(base, "")
            var_mw[new] = list(var_mw.get(base, []))
            # gin: r.Group("/api", authMiddleware())
            open_idx = masked.find("(", m.end() - 2)
            args_text, close = _call_args(sf, open_idx)
            if close > 0:
                for a in lexer.split_top_level(args_text, ",")[1:]:
                    var_mw[new].append(a.strip())
            continue
        if kind == "routefn":
            recv, inner = m.group(1), m.group(4)
            path = sf.text[m.start(3):m.end(3)] if m.group(3) is not None else ""
            brace = m.end() - 1
            close = lexer.match_bracket(masked, brace)
            if close < 0:
                continue
            vp = dict(var_prefix)
            vp[inner] = join_paths(var_prefix.get(recv, ""), path) if path else var_prefix.get(recv, "")
            vm = dict(var_mw)
            vm[inner] = list(var_mw.get(recv, []))
            _scan_scope(index, sf, brace + 1, close, vp, vm, hints, seeds, visited, scope_name)
            skip_until = close
            continue
        if kind == "mount":
            recv, target = m.group(1), m.group(3)
            path = sf.text[m.start(2):m.end(2)]
            fn, _ = _resolve(index, sf, target, hints, package)
            if fn is None or fn.qualname in visited:
                continue
            visited.add(fn.qualname)
            vp = {"*": join_paths(var_prefix.get(recv, ""), path)}
            th = _local_types(fn.file, fn.start, fn.end, fn.params)
            _scan_scope(index, fn.file, fn.start, fn.end, vp, {"*": list(var_mw.get(recv, []))}, th, seeds, visited, fn.name)
            continue
        if kind == "use":
            recv = m.group(1)
            open_idx = m.end() - 1
            args_text, close = _call_args(sf, open_idx)
            if close > 0:
                var_mw.setdefault(recv, []).extend(a.strip() for a in lexer.split_top_level(args_text, ",") if a.strip())
            continue
        # route
        recv, verb_word = m.group(1), m.group(2)
        open_idx = m.end() - 1
        args_text, close = _call_args(sf, open_idx)
        if close < 0:
            continue
        args = lexer.split_top_level(args_text, ",")
        if not args:
            continue
        verbs: List[str] = []
        path_arg_index = 0
        if verb_word in ("Method", "MethodFunc"):
            v = _string_arg(args[0]) or ""
            hv = _HTTP_VERB_CONST.search(args[0])
            verbs = [(hv.group(1).upper() if hv else v.upper()) or "ANY"]
            path_arg_index = 1
        else:
            verbs = [VERB_METHODS.get(verb_word, "ANY")]
        if len(args) <= path_arg_index:
            continue
        path = _string_arg(args[path_arg_index])
        if path is None:
            continue
        # go 1.22 "GET /orders/{id}" patterns
        pm = re.match(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)$", path)
        if pm:
            verbs, path = [pm.group(1)], pm.group(2)
        # gorilla: .Methods("GET", "POST") chained after the call
        chain = masked[close + 1:close + 200]
        mm = _METHODS_CHAIN_RX.search(chain)
        if mm and verbs == ["ANY"]:
            src_chain = sf.text[close + 1:close + 200]
            mm2 = _METHODS_CHAIN_RX.search(src_chain)
            names = re.findall(r"\"(\w+)\"|http\.Method(\w+)", mm2.group(1) if mm2 else "")
            verbs = [(a or b).upper() for a, b in names] or ["ANY"]
        handler_args = args[path_arg_index + 1:]
        if not handler_args:
            continue
        handler_expr = handler_args[-1].strip()
        mws = [a.strip() for a in handler_args[:-1]] + list(var_mw.get(recv, [])) + list(var_mw.get("*", []))
        # chi: r.With(mw).Get(...)
        wm = re.search(r"\.With\(((?:[^()]|\([^()]*\))*)\)", sf.text[m.start():m.end()])
        if wm:
            mws.extend(a.strip() for a in lexer.split_top_level(wm.group(1), ","))
        prefix = var_prefix.get(recv, var_prefix.get("*", ""))
        props: Dict = {}
        for mw in mws:
            props.setdefault(_classify_mw(mw), []).append(mw[:60])
        pp = re.findall(r"\{(\w+)[^}]*\}|:(\w+)", path)
        pp = [a or b for a, b in pp]
        if pp:
            props["path_params"] = pp
        # handler
        if handler_expr.startswith("func"):
            abs_start = sf.text.find(handler_expr, open_idx)
            fn = _inline_fn(sf, handler_expr, abs_start, f"{verbs[0].lower()} {path}") if abs_start >= 0 else None
            hname = f"{scope_name} inline handler"
        else:
            fn, hname = _resolve(index, sf, handler_expr, hints, package)
        index.note_framework(fw)
        for verb in verbs:
            ep = Endpoint(method=verb, path=join_paths(prefix, path), handler=Handler(hname, sf.path, sf.lines.line(m.start()), "go"),
                          framework=fw, properties=dict(props))
            if fn is None:
                ep.warnings.append(f"handler `{handler_expr}` could not be located in the repo; flow not traced")
            seeds.append(EndpointSeed(endpoint=ep, fn=fn))


def _call_args(sf, open_idx: int) -> Tuple[str, int]:
    close = lexer.match_bracket(sf.masked, open_idx)
    if close < 0:
        return "", -1
    return sf.text[open_idx + 1:close], close
