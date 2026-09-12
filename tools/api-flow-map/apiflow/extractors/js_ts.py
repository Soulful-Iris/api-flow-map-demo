"""Endpoints for JavaScript/TypeScript.

Covers
- Express / Koa-router / Fastify / Hono / Elysia style `router.get(path, ...mw, handler)`
  including `router.route(path).get(...)`, `fastify.route({method,url,handler})`
  and mount prefixes: `app.use('/api', ordersRouter)`, `fastify.register(plugin, {prefix})`
- NestJS `@Controller()` classes with `@Get()`-style decorators
- Next.js `pages/api/**` and `app/**/route.ts`
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Set, Tuple

from .. import lexer
from ..model import Endpoint, Handler, join_paths
from .annotations import as_list, find_annotations, parse_args
from .base import CodeIndex, EndpointSeed, FunctionDef, parse_params

VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "del": "DELETE",
         "all": "ANY", "options": "OPTIONS", "head": "HEAD"}
NEST_VERBS = {"Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE", "All": "ANY",
              "Options": "OPTIONS", "Head": "HEAD"}
DEFAULT_ROUTER_NAMES = {"app", "router", "server", "api", "routes", "fastify", "r", "v1", "v2", "koa", "hono", "route"}
HTTP_CLIENT_NAMES = {"axios", "http", "https", "fetch", "got", "superagent", "request", "client", "httpClient", "ky", "needle", "instance", "api"}

_ROUTER_CTOR_RX = re.compile(
    r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::\s*[\w.<>]+\s*)?=\s*(?:await\s+)?"
    r"(?:express\s*\(\s*\)|express\.Router\s*\((?P<expargs>[^)]*)\)|Router\s*\((?P<rargs>[^)]*)\)|new\s+(?:Router|KoaRouter)\s*\((?P<kargs>[^)]*)\)"
    r"|new\s+Hono\s*\([^)]*\)|new\s+Koa\s*\(\s*\)|[Ff]astify\s*\([^)]*\)|new\s+Elysia\s*\([^)]*\)|require\(['\"]express['\"]\)\s*\(\s*\)"
    r"|new\s+Hapi\.Server\s*\([^)]*\)|Hapi\.server\s*\([^)]*\))")
_ROUTE_CALL_RX = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\.\s*(get|post|put|patch|delete|del|all|options|head|use|route|register|route)\s*\(")
_GLOBAL_PREFIX_RX = re.compile(r"setGlobalPrefix\(\s*['\"`]([^'\"`]+)['\"`]")
_EXPORT_ROUTER_RX = re.compile(r"(?:module\.exports\s*=\s*|export\s+default\s+)([A-Za-z_$][\w$]*)\s*;?")
_NAMED_EXPORT_RX = re.compile(r"export\s+(?:const|let|var|function|class|async\s+function)\s+([A-Za-z_$][\w$]*)")
_EXPORTS_PROP_RX = re.compile(r"(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)?")
_EXPORTS_OBJ_RX = re.compile(r"module\.exports\s*=\s*\{([^}]*)\}")

AUTH_RX = re.compile(r"auth|authn|authz|authenticat|authoriz|isAuthenticated|requireAuth|requireRole|checkRole|permit|passport|jwt|verifyToken|ensureLoggedIn|protect|guard|roles?\b|hasRole|isAdmin|allowRoles|session|apiKey|bearer|oauth|acl\b|rbac|scopes?", re.I)
VALIDATE_RX = re.compile(r"validat|celebrate|checkSchema|\bbody\(|\bparam\(|\bquery\(|schema|zod|joi|yup|\bcheck\(|sanitiz|ajv", re.I)
RATE_RX = re.compile(r"rateLimit|limiter|throttl|slowDown", re.I)
CACHE_RX = re.compile(r"cache", re.I)
WRAPPERS = {"asyncHandler", "wrap", "catchAsync", "tryCatch", "asyncMiddleware", "asyncWrap", "wrapAsync", "handleAsync",
            "catchErrors", "safe", "withErrorHandling", "expressAsyncHandler", "ah", "asyncify", "promisify"}


class _RouteReg:
    __slots__ = ("file", "var", "verb", "path", "args", "line", "arg_start", "handler_text", "middleware")

    def __init__(self, file, var, verb, path, args, line, arg_start):
        self.file, self.var, self.verb, self.path, self.args, self.line, self.arg_start = file, var, verb, path, args, line, arg_start
        self.handler_text = ""
        self.middleware: List[str] = []


def _router_vars(sf) -> Tuple[Set[str], Dict[str, str]]:
    names: Set[str] = set()
    prefixes: Dict[str, str] = {}
    for m in _ROUTER_CTOR_RX.finditer(sf.text):
        names.add(m.group(1))
        for grp in ("expargs", "rargs", "kargs"):
            try:
                a = m.group(grp)
            except IndexError:
                a = None
            if a and "prefix" in a:
                pm = re.search(r"prefix\s*:\s*['\"`]([^'\"`]+)", a)
                if pm:
                    prefixes[m.group(1)] = pm.group(1)
    for m in re.finditer(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)\.(?:Router|router)\s*\(", sf.text):
        names.add(m.group(1))
    return names, prefixes


def _call_args(sf, open_idx: int) -> Tuple[str, int]:
    close = lexer.match_bracket(sf.masked, open_idx)
    if close < 0:
        return "", -1
    return sf.text[open_idx + 1:close], close


def _string_value(arg: str) -> Optional[str]:
    a = arg.strip()
    if len(a) >= 2 and a[0] == a[-1] and a[0] in "\"'`":
        return a[1:-1]
    return None


def _path_like(arg: str) -> Optional[str]:
    v = _string_value(arg)
    if v is None:
        # array of paths -> first
        if arg.strip().startswith("["):
            inner = lexer.split_top_level(arg.strip()[1:-1], ",")
            if inner:
                return _string_value(inner[0])
        return None
    if v.startswith("/") or v == "" or v.startswith(":") or v.startswith("*"):
        return v or "/"
    if v.startswith("http"):
        return None
    return None


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


def _synthetic_fn(sf, arg_text: str, abs_start: int, name: str) -> Optional[FunctionDef]:
    """Build a FunctionDef for an inline handler `async (req, res) => { ... }`."""
    masked = sf.masked
    seg = masked[abs_start:abs_start + len(arg_text)]
    m = re.search(r"(=>|\)|function\s*\w*\s*\([^)]*\))\s*\{", seg)
    if not m:
        # expression-bodied arrow: `(req) => res.json(x)` -> treat the expression as the body
        m2 = re.search(r"=>\s*", seg)
        if not m2:
            return None
        start = abs_start + m2.end()
        end = abs_start + len(arg_text)
        return FunctionDef(name=name, file=sf, start=start, end=end, line=sf.lines.line(abs_start),
                           params=_arrow_params(arg_text), sig=arg_text[:m2.start()])
    brace = abs_start + m.end() - 1
    close = lexer.match_bracket(masked, brace)
    if close < 0:
        return None
    return FunctionDef(name=name, file=sf, start=brace + 1, end=close, line=sf.lines.line(abs_start),
                       params=_arrow_params(arg_text), sig=arg_text[:m.start() + 1])


def _arrow_params(text: str) -> str:
    m = re.match(r"\s*(?:async\s*)?(?:function\s*\w*\s*)?\(([^)]*)\)", text)
    if m:
        return m.group(1)
    m = re.match(r"\s*(?:async\s*)?([A-Za-z_$][\w$]*)\s*=>", text)
    return m.group(1) if m else ""


def _unwrap(expr: str) -> str:
    """asyncHandler(getOrder) -> getOrder ; ctrl.get.bind(ctrl) -> ctrl.get"""
    e = expr.strip()
    e = re.sub(r"\.bind\([^)]*\)\s*$", "", e)
    m = re.match(r"^([A-Za-z_$][\w$]*)\s*\((.+)\)\s*$", e, re.S)
    if m and (m.group(1) in WRAPPERS or re.match(r"^(?:async|wrap|catch|safe|try)", m.group(1), re.I)):
        return _unwrap(m.group(2))
    return e


def _resolve_handler_ref(index: CodeIndex, sf, expr: str) -> Optional[FunctionDef]:
    expr = _unwrap(expr)
    if not re.match(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$", expr):
        return None
    parts = expr.split(".")
    if len(parts) == 1:
        fn = index.function_in_file(sf.path, parts[0])
        if fn:
            return fn
        imp = index.resolve_import(sf.path, parts[0])
        if imp and imp[0]:
            target, exported = imp
            name = parts[0] if exported in ("default", "*") else exported
            fn = _exported_function(index, target, name)
            if fn:
                return fn
        return index.unique_function(parts[0], sf.path)
    recv, meth = parts[0], parts[-1]
    if recv == "this":
        return None
    imp = index.resolve_import(sf.path, recv)
    if imp and imp[0]:
        target, exported = imp
        fn = _exported_function(index, target, meth)
        if fn:
            return fn
    hint = index.module_vars.get(sf.path, {}).get(recv)
    if hint:
        fn = index.method_of(hint, meth, sf.path)
        if fn:
            return fn
    # receiver may itself be a class name (static) or an imported class instance
    fn = index.method_of(recv, meth, sf.path)
    if fn:
        return fn
    # controller object literal in another file: `const controller = { getOrder: ... }`
    return index.unique_function(meth, sf.path)


def _exported_function(index: CodeIndex, target_path: str, name: str) -> Optional[FunctionDef]:
    fn = index.function_in_file(target_path, name)
    if fn:
        return fn
    exports = index.exports.get(target_path, {})
    local = exports.get(name)
    if local:
        fn = index.function_in_file(target_path, local)
        if fn:
            return fn
    # default-exported class instance / singleton
    default_local = exports.get("default")
    if default_local:
        fn = index.method_of(default_local, name, target_path)
        if fn:
            return fn
        hint = index.module_vars.get(target_path, {}).get(default_local)
        if hint:
            fn = index.method_of(hint, name, target_path)
            if fn:
                return fn
    for f in index.by_name.get(name, []):
        if f.file.path == target_path and f.has_body:
            return f
    return None


def index_js_exports(index: CodeIndex, files) -> None:
    for sf in files:
        ex: Dict[str, str] = {}
        for m in _EXPORT_ROUTER_RX.finditer(sf.text):
            ex["default"] = m.group(1)
        for m in _NAMED_EXPORT_RX.finditer(sf.text):
            ex[m.group(1)] = m.group(1)
        for m in _EXPORTS_PROP_RX.finditer(sf.text):
            ex[m.group(1)] = m.group(2) or m.group(1)
        for m in _EXPORTS_OBJ_RX.finditer(sf.text):
            for part in m.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                if ":" in part:
                    k, v = [x.strip() for x in part.split(":", 1)]
                    ex[k] = v if re.match(r"^[A-Za-z_$][\w$]*$", v) else k
                else:
                    ex[part] = part
        for m in re.finditer(r"export\s*\{([^}]*)\}", sf.text):
            for part in m.group(1).split(","):
                part = part.strip()
                if " as " in part:
                    loc, exp = [x.strip() for x in part.split(" as ", 1)]
                    ex[exp] = loc
                elif part:
                    ex[part] = part
        index.exports[sf.path] = ex


# ------------------------------------------------------------------------------ routers

def _collect_routes(index: CodeIndex, sf, router_names: Set[str]) -> Tuple[List[_RouteReg], List[Tuple[str, str, List[str], int]], Dict[str, str]]:
    """Scan one file. Returns (routes, mounts[(var, prefix, target_exprs, line)], local_prefixes)."""
    routes: List[_RouteReg] = []
    mounts: List[Tuple[str, str, List[str], int]] = []
    masked = sf.masked
    known = router_names | DEFAULT_ROUTER_NAMES
    for m in _ROUTE_CALL_RX.finditer(masked):
        recv, verb = m.group(1), m.group(2)
        if recv in HTTP_CLIENT_NAMES and recv not in router_names:
            continue
        if recv not in known and verb != "route":
            # allow `this.router.get(...)` style? receiver 'router' after 'this.' handled by regex (recv='router')
            continue
        open_idx = m.end() - 1
        args_text, close = _call_args(sf, open_idx)
        if close < 0:
            continue
        args = lexer.split_top_level(args_text, ",")
        line = sf.lines.line(m.start())
        if verb == "use":
            if not args:
                continue
            p = _path_like(args[0])
            targets = [a.strip() for a in (args[1:] if p is not None else args)]
            if p is None and len(args) >= 1 and args[0].strip() in ("", ):
                continue
            mounts.append((recv, p or "", targets, line))
            continue
        if verb == "register":   # fastify.register(plugin, { prefix: '/x' })
            if not args:
                continue
            prefix = ""
            if len(args) > 1:
                pm = re.search(r"prefix\s*:\s*['\"`]([^'\"`]+)", args[1])
                prefix = pm.group(1) if pm else ""
            mounts.append((recv, prefix, [args[0].strip()], line))
            continue
        if verb == "route":
            if not args:
                continue
            obj = args[0].strip()
            if obj.startswith("{"):   # fastify/hapi object form
                method = re.search(r"method\s*:\s*(\[[^\]]*\]|['\"`]\w+['\"`])", obj)
                url = re.search(r"(?:url|path)\s*:\s*['\"`]([^'\"`]+)", obj)
                handler = re.search(r"handler\s*:\s*", obj)
                if not (method and url):
                    continue
                verbs = re.findall(r"['\"`](\w+)['\"`]", method.group(1))
                for v in verbs:
                    reg = _RouteReg(sf, recv, v.upper(), url.group(1), [obj], line, open_idx + 1)
                    if handler:
                        # handler text = value of `handler:` key
                        hstart = handler.end()
                        # take the rest of the object as handler expression (split by top-level commas later)
                        rest = obj[hstart:]
                        parts = lexer.split_top_level(rest, ",")
                        reg.handler_text = parts[0] if parts else ""
                        reg.arg_start = open_idx + 1 + (args_text.find(obj)) + hstart
                    for key in ("preHandler", "preValidation", "onRequest"):
                        km = re.search(key + r"\s*:\s*(\[[^\]]*\]|[\w.]+(?:\([^)]*\))?)", obj)
                        if km:
                            reg.middleware.extend(re.findall(r"[A-Za-z_$][\w$.]*", km.group(1)))
                    routes.append(reg)
                continue
            # router.route('/x').get(h).post(h2)
            p = _path_like(obj)
            if p is None:
                continue
            j = close + 1
            chain_end = j
            while True:
                cm = re.match(r"\s*\.\s*(get|post|put|patch|delete|del|all|options|head)\s*\(", masked[j:j + 60])
                if not cm:
                    break
                oi = j + cm.end() - 1
                a_text, c2 = _call_args(sf, oi)
                if c2 < 0:
                    break
                reg = _RouteReg(sf, recv, VERBS[cm.group(1)], p, lexer.split_top_level(a_text, ","), sf.lines.line(oi), oi + 1)
                routes.append(reg)
                j = c2 + 1
            continue
        # plain verb call
        if not args:
            continue
        p = _path_like(args[0])
        if p is None:
            continue
        reg = _RouteReg(sf, recv, VERBS[verb], p, args[1:], line, open_idx + 1 + len(args_text) - len(args_text.lstrip()) )
        # arg_start must point at the start of args[1] inside args_text; compute offsets precisely
        reg.arg_start = open_idx + 1
        routes.append(reg)
    return routes, mounts, {}


def _arg_offsets(args_text: str) -> List[int]:
    """Start offsets of each top-level argument inside args_text."""
    offs, depth, quote, i, cur_start = [], 0, "", 0, 0
    started = False
    while i < len(args_text):
        c = args_text[i]
        if not started and not c.isspace():
            offs.append(i)
            started = True
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "\"'`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            started = False
        i += 1
    return offs


def extract(index: CodeIndex, files) -> List[EndpointSeed]:
    seeds: List[EndpointSeed] = []
    files = list(files)
    index_js_exports(index, files)
    global_prefix = ""
    for sf in files:
        gm = _GLOBAL_PREFIX_RX.search(sf.text)
        if gm:
            global_prefix = gm.group(1)

    # ---- pass 1: routes and mounts per file
    file_routes: Dict[str, List[_RouteReg]] = {}
    edges: List[Tuple[Tuple[str, str], str, Tuple[str, str], int, List[str]]] = []   # ((file,var), prefix, (file,var), line, middleware)
    file_router_vars: Dict[str, Set[str]] = {}
    var_prefix: Dict[Tuple[str, str], str] = {}
    mw_by_var: Dict[Tuple[str, str], List[str]] = {}
    inherited_mw: Dict[Tuple[str, str], List[str]] = {}
    for sf in files:
        rvars, local_prefixes = _router_vars(sf)
        file_router_vars[sf.path] = rvars
        for v, p in local_prefixes.items():
            var_prefix[(sf.path, v)] = p
        routes, mounts, _ = _collect_routes(index, sf, rvars)
        if routes:
            file_routes[sf.path] = routes
            index.note_framework("express" if not re.search(r"\bfastify\b", sf.text) else "fastify")
        for recv, prefix, targets, line in mounts:
            router_targets: List[Tuple[Optional[str], str]] = []
            mws: List[str] = []
            for target in targets:
                target = _unwrap(target)
                rm = re.match(r"^require\(\s*['\"]([^'\"]+)['\"]\s*\)$", target)
                tfile: Optional[str] = None
                tvar = ""
                if rm:
                    t = index.repo.resolve_module(sf.path, rm.group(1))
                    tfile = t.path if t else None
                elif re.match(r"^[A-Za-z_$][\w$]*$", target):
                    if target in rvars:
                        tfile, tvar = sf.path, target
                    else:
                        imp = index.resolve_import(sf.path, target)
                        if imp and imp[0]:
                            exported = imp[1]
                            cand_file = imp[0]
                            cand_var = index.exports.get(cand_file, {}).get("default" if exported in ("default", "*") else exported, "" if exported in ("default", "*") else exported)
                            # only a router-ish module counts as a mount target
                            trv = file_router_vars.get(cand_file)
                            if trv is None and cand_file in index.repo.by_path:
                                trv = _router_vars(index.repo.by_path[cand_file])[0]
                            if trv and (cand_var in trv or not cand_var):
                                tfile, tvar = cand_file, (cand_var or sorted(trv)[0])
                            elif trv:
                                tfile, tvar = cand_file, sorted(trv)[0]
                elif re.match(r"^[A-Za-z_$][\w$]*\.[A-Za-z_$][\w$]*$", target):
                    ns, prop = target.split(".")
                    imp = index.resolve_import(sf.path, ns)
                    if imp and imp[0]:
                        tfile = imp[0]
                        tvar = index.exports.get(tfile, {}).get(prop, prop)
                if tfile is None:
                    mws.append(target)
                    continue
                if not tvar:
                    tvar = index.exports.get(tfile, {}).get("default", "")
                    if not tvar:
                        rv = file_router_vars.get(tfile) or (_router_vars(index.repo.by_path[tfile])[0] if tfile in index.repo.by_path else set())
                        tvar = sorted(rv)[0] if rv else "router"
                router_targets.append((tfile, tvar))
            if not router_targets:
                # router.use(auth) : middleware for every route on this router var
                mw_by_var.setdefault((sf.path, recv), []).extend(mws)
                continue
            for tfile, tvar in router_targets:
                edges.append(((sf.path, recv), prefix, (tfile, tvar), line, list(mws)))

    # ---- pass 2: resolve full prefixes by walking mounts from roots
    targets = {e[2] for e in edges}
    full_prefix: Dict[Tuple[str, str], str] = {}
    multi: Dict[Tuple[str, str], List[str]] = {}

    def walk(node: Tuple[str, str], prefix: str, seen: Set[Tuple[str, str]], mws: List[str]) -> None:
        if node in seen:
            return
        seen = seen | {node}
        if node in full_prefix and full_prefix[node] != prefix:
            multi.setdefault(node, []).append(prefix)
            return
        full_prefix.setdefault(node, prefix)
        inherited_mw.setdefault(node, list(mws) + mw_by_var.get(node, []))
        for (src, p, dst, _line, edge_mw) in edges:
            if src == node:
                walk(dst, join_paths(prefix, var_prefix.get(dst, ""), p) if p or var_prefix.get(dst) else prefix, seen,
                     inherited_mw[node] + edge_mw)

    all_nodes = {e[0] for e in edges} | targets | {(f, v) for f, rs in file_routes.items() for v in {r.var for r in rs}}
    for node in sorted(all_nodes):
        if node not in targets:
            walk(node, join_paths(global_prefix, var_prefix.get(node, "")) if (global_prefix or var_prefix.get(node)) else "", set(), [])

    # ---- pass 3: endpoints
    for path, routes in sorted(file_routes.items()):
        sf = index.repo.by_path[path]
        for r in routes:
            prefix = full_prefix.get((path, r.var), var_prefix.get((path, r.var), ""))
            ep_path = join_paths(prefix, r.path)
            handler_fn: Optional[FunctionDef] = None
            props: Dict = {}
            mws: List[str] = list(r.middleware)
            args = r.args
            if r.handler_text:
                handler_expr = r.handler_text
                mw_args = []
            elif args:
                handler_expr = args[-1]
                mw_args = args[:-1]
            else:
                handler_expr, mw_args = "", []
            for mw in mw_args:
                mws.append(mw.strip())
            mws = inherited_mw.get((path, r.var), mw_by_var.get((path, r.var), [])) + mws
            for mw in mws:
                kind = _classify_mw(mw)
                short = re.sub(r"\s+", " ", mw)[:60]
                props.setdefault(kind, []).append(short)
            he = handler_expr.strip()
            if he:
                if re.match(r"^(?:async\s*)?(?:\(|function\b|[A-Za-z_$][\w$]*\s*=>)", he):
                    # locate the handler expression's absolute start inside the file
                    abs_start = sf.text.find(he, r.arg_start) if r.arg_start >= 0 else sf.text.find(he)
                    if abs_start < 0:
                        abs_start = sf.text.find(he)
                    handler_fn = _synthetic_fn(sf, he, abs_start, f"{r.verb.lower()} {r.path}") if abs_start >= 0 else None
                    hname = "(inline)"
                else:
                    handler_fn = _resolve_handler_ref(index, sf, he)
                    hname = _unwrap(he)
                    if hname.startswith("this."):
                        hname = hname[5:]
            else:
                hname = "(none)"
            ep = Endpoint(method=r.verb, path=ep_path,
                          handler=Handler(hname if hname != "(inline)" else f"{os.path.basename(sf.path)} inline handler", sf.path, r.line, sf.lang),
                          framework=index_framework_name(sf), properties=props)
            if (path, r.var) in multi:
                ep.warnings.append("router is mounted at multiple prefixes; showing the first: " + ", ".join(multi[(path, r.var)]))
            if handler_fn is None:
                ep.warnings.append(f"handler `{hname}` could not be located in the repo; flow not traced")
            seeds.append(EndpointSeed(endpoint=ep, fn=handler_fn))

    seeds.extend(_extract_nest(index, files, global_prefix))
    seeds.extend(_extract_next(index, files))
    return seeds


def index_framework_name(sf) -> str:
    t = sf.text
    if re.search(r"\bfastify\b", t):
        return "fastify"
    if re.search(r"\bkoa\b", t, re.I):
        return "koa"
    if re.search(r"\bhono\b", t, re.I):
        return "hono"
    if re.search(r"\bhapi\b", t, re.I):
        return "hapi"
    return "express"


# ------------------------------------------------------------------------------ NestJS

def _extract_nest(index: CodeIndex, files, global_prefix: str) -> List[EndpointSeed]:
    seeds: List[EndpointSeed] = []
    for sf in files:
        if sf.lang != "typescript" and "@Controller" not in sf.text:
            continue
        blocks = index.blocks.get(sf.path, [])
        for b in blocks:
            if b.kind != "class":
                continue
            canns = find_annotations(b.sig)
            cnames = {n for n, _ in canns}
            if "Controller" not in cnames:
                continue
            index.note_framework("nestjs")
            base = ""
            class_auth: List[str] = []
            version = ""
            for n, a in canns:
                pa = parse_args(a)
                if n == "Controller":
                    v = pa.get("value")
                    if isinstance(v, str):
                        base = v
                    elif isinstance(v, dict):
                        base = str(v.get("path", ""))
                    else:
                        pm = re.search(r"path\s*:\s*['\"`]([^'\"`]+)", a)
                        base = pm.group(1) if pm else ""
                    vm = re.search(r"version\s*:\s*['\"`]?([\w.]+)", a)
                    version = vm.group(1) if vm else ""
                elif n in ("UseGuards", "Roles", "Auth", "Authorize"):
                    class_auth.append(f"@{n}({a.strip()})" if a.strip() else f"@{n}")
            cls = index.class_named(b.name, sf.path)
            for fb in blocks:
                if fb.kind != "function" or fb.parent is not b:
                    continue
                anns = find_annotations(fb.sig)
                verbs, paths = [], [""]
                props: Dict = {}
                auth = list(class_auth)
                for n, a in anns:
                    pa = parse_args(a)
                    if n in NEST_VERBS:
                        verbs.append(NEST_VERBS[n])
                        v = pa.get("value")
                        paths = [str(x) for x in as_list(v)] if v is not None else [""]
                    elif n in ("UseGuards", "Roles", "Auth", "Authorize", "Permissions", "Scopes"):
                        auth.append(f"@{n}({a.strip()})" if a.strip() else f"@{n}")
                    elif n in ("Public", "AllowAnonymous", "SkipAuth"):
                        auth.append("public")
                    elif n == "HttpCode":
                        try:
                            props["status"] = int(str(pa.get("value")))
                        except (TypeError, ValueError):
                            pass
                    elif n == "ApiOperation":
                        sm = re.search(r"summary\s*:\s*['\"`]([^'\"`]+)", a)
                        if sm:
                            props["spec_summary"] = sm.group(1)
                    elif n == "ApiResponse":
                        sm = re.search(r"status\s*:\s*(\d{3})", a)
                        if sm:
                            props.setdefault("declared_responses", []).append(int(sm.group(1)))
                    elif n in ("UseInterceptors",) and "Cache" in a:
                        props.setdefault("cache", []).append(a.strip())
                    elif n in ("Throttle", "RateLimit"):
                        props.setdefault("rate_limit", []).append(f"@{n}({a.strip()})")
                    elif n in ("UsePipes",) and "Validation" in a:
                        props.setdefault("validation", []).append(a.strip())
                    elif n == "Version":
                        version = str(pa.get("value", version))
                    elif n == "Deprecated" or n == "ApiDeprecated":
                        props["deprecated"] = True
                if not verbs:
                    continue
                path_params, query, body, headers = [], [], "", []
                for ann_txt, ptype, pname in parse_params(sf.lang, fb.params):
                    for n, a in find_annotations(ann_txt):
                        pa = parse_args(a)
                        v = pa.get("value")
                        if n == "Param":
                            path_params.append(str(v or pname))
                        elif n == "Query":
                            query.append(str(v or pname))
                        elif n == "Body":
                            body = re.sub(r"<.*", "", ptype).split(".")[-1] or "body"
                        elif n == "Headers":
                            headers.append(str(v or pname))
                        elif n in ("User", "CurrentUser", "Req", "Request") and n != "Request":
                            props.setdefault("auth_context", []).append(pname)
                if path_params:
                    props["path_params"] = path_params
                if query:
                    props["query_params"] = query
                if body:
                    props["body"] = body
                if headers:
                    props["headers"] = headers
                if auth:
                    props["auth"] = auth
                fn = cls.methods.get(fb.name) if cls else None
                vprefix = f"/v{version}" if version and not version.startswith("v") else (f"/{version}" if version else "")
                for p in paths:
                    for verb in verbs:
                        ep = Endpoint(method=verb, path=join_paths(global_prefix, vprefix, base, p),
                                      handler=Handler(f"{b.name}.{fb.name}", sf.path, fb.line, sf.lang),
                                      framework="nestjs", properties=dict(props))
                        seeds.append(EndpointSeed(endpoint=ep, fn=fn, owner_class=cls))
    return seeds


# ------------------------------------------------------------------------------ Next.js

def _extract_next(index: CodeIndex, files) -> List[EndpointSeed]:
    seeds: List[EndpointSeed] = []
    for sf in files:
        p = sf.path
        m = re.match(r"^(?:src/)?pages/api/(.+?)\.(?:[cm]?[jt]sx?)$", p)
        if m:
            route = "/api/" + m.group(1)
            route = re.sub(r"/index$", "", route)
            route = re.sub(r"\[\.\.\.(\w+)\]", r"{\1*}", route)
            route = re.sub(r"\[(\w+)\]", r"{\1}", route)
            fn = None
            ex = index.exports.get(p, {})
            local = ex.get("default")
            if local:
                fn = index.function_in_file(p, local)
            if fn is None:
                for f in index.by_name.get("handler", []):
                    if f.file.path == p:
                        fn = f
            if fn is None:
                continue
            index.note_framework("nextjs")
            verbs = sorted(set(re.findall(r"req\.method\s*===?\s*['\"](\w+)['\"]", sf.text))) or ["ANY"]
            for v in verbs:
                ep = Endpoint(method=v, path=route, handler=Handler(f"{sf.stem}.{fn.name}", p, fn.line, sf.lang), framework="nextjs")
                seeds.append(EndpointSeed(endpoint=ep, fn=fn))
            continue
        m = re.match(r"^(?:src/)?app/(.+?)/route\.(?:[cm]?[jt]sx?)$", p)
        if m:
            route = "/" + m.group(1)
            route = re.sub(r"/\([^)]*\)", "", route)  # route groups
            route = re.sub(r"\[\.\.\.(\w+)\]", r"{\1*}", route)
            route = re.sub(r"\[(\w+)\]", r"{\1}", route)
            for verb in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
                fn = index.function_in_file(p, verb)
                if fn is None:
                    continue
                index.note_framework("nextjs")
                ep = Endpoint(method=verb, path=route, handler=Handler(f"{sf.stem}.{verb}", p, fn.line, sf.lang), framework="nextjs")
                seeds.append(EndpointSeed(endpoint=ep, fn=fn))
    return seeds
