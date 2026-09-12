"""Endpoints for Python web frameworks, parsed with the real `ast` module.

Covers Flask (app/blueprint routes, MethodView, flask-restful resources),
FastAPI (app/APIRouter with prefixes, Depends-based auth), Django REST
(urls.py `path()` -> views, DRF routers/ViewSets/@action, @api_view).
"""
from __future__ import annotations

import ast
import os
import re
from typing import Dict, List, Optional, Tuple

from ..lexer import Arm, Stmt
from ..model import Endpoint, Handler, join_paths
from .base import ClassDef, CodeIndex, EndpointSeed, FunctionDef

VERB_DECOS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "options": "OPTIONS", "head": "HEAD"}
DRF_ACTIONS = {"list": ("GET", ""), "create": ("POST", ""), "retrieve": ("GET", "/{pk}"), "update": ("PUT", "/{pk}"),
               "partial_update": ("PATCH", "/{pk}"), "destroy": ("DELETE", "/{pk}")}
AUTH_DECOS = {"login_required", "jwt_required", "roles_required", "roles_accepted", "permission_required", "require_auth",
              "requires_auth", "auth_required", "token_required", "admin_required", "authenticated", "permission_classes",
              "authentication_classes", "requires_scope", "require_role", "login_exempt", "csrf_exempt", "staff_member_required",
              "user_passes_test", "has_permission", "fresh_jwt_required"}
AUTH_DEP_RX = re.compile(r"auth|user|token|jwt|role|permission|scope|login|session|api_key|apikey|verify|require|admin|principal|identity", re.I)


def _module_name(path: str) -> str:
    p = path[:-3] if path.endswith(".py") else path
    if p.endswith("/__init__"):
        p = p[:-9]
    return p.replace("/", ".")


def _resolve_dotted(index: CodeIndex, from_file: str, module: str, level: int) -> Optional[str]:
    repo = index.repo
    if level and level > 0:
        base = os.path.dirname(from_file)
        for _ in range(level - 1):
            base = os.path.dirname(base)
        rel = module.replace(".", "/")
        cand = f"{base}/{rel}" if rel else base
        cand = cand.strip("/")
        for suffix in (".py", "/__init__.py"):
            if cand + suffix in repo.by_path:
                return cand + suffix
        return None
    rel = module.replace(".", "/")
    roots = ["", "src/", "app/", "api/", "backend/", "server/"]
    for root in roots:
        for suffix in (".py", "/__init__.py"):
            cand = root + rel + suffix
            if cand in repo.by_path:
                return cand
    # module may be a package path whose prefix differs: try suffix match on the last two segments
    tail = "/".join(module.split(".")[-2:])
    for p in repo.by_path:
        if p.endswith(tail + ".py") or p.endswith(tail + "/__init__.py"):
            return p
    return None


def index_python_file(sf, index: CodeIndex) -> Optional[ast.Module]:
    try:
        tree = ast.parse(sf.text)
    except SyntaxError as exc:
        index.repo.diagnostics.append(f"{sf.path}: python syntax error at line {exc.lineno}; file skipped")
        return None
    module = _module_name(sf.path)
    imap: Dict[str, Tuple[Optional[str], str]] = {}
    hints: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                imap[local] = (_resolve_dotted(index, sf.path, alias.name, 0), "*")
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_dotted(index, sf.path, node.module or "", node.level or 0)
            for alias in node.names:
                local = alias.asname or alias.name
                # `from .routes import orders` may import a submodule rather than a name
                sub = None
                if target and target.endswith("__init__.py"):
                    sub_cand = os.path.dirname(target) + "/" + alias.name + ".py"
                    if sub_cand in index.repo.by_path:
                        sub = sub_cand
                imap[local] = ((sub, "*") if sub else (target, alias.name))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            v = node.value
            if isinstance(v, ast.Call):
                fname = _name_of(v.func)
                hints[node.targets[0].id] = fname.split(".")[-1]
    index.imports[sf.path] = imap
    index.module_vars[sf.path] = hints

    def add_fn(node, owner: str = "") -> FunctionDef:
        fn = FunctionDef(name=node.name, file=sf, owner=owner, line=node.lineno, params=_params_text(node),
                         sig=_decorators_text(node), has_body=True, package=module, py_node=node)
        index.add_function(fn)
        return fn

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add_fn(node)
        elif isinstance(node, ast.ClassDef):
            bases = [_name_of(b).split(".")[-1] for b in node.bases]
            cls = ClassDef(name=node.name, file=sf, kind="class", extends=bases, line=node.lineno, sig=_decorators_text(node))
            index.add_class(cls)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn = add_fn(item, node.name)
                    cls.methods[item.name] = fn
                    for sub in ast.walk(item):
                        if isinstance(sub, ast.Assign) and len(sub.targets) == 1:
                            t = sub.targets[0]
                            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self" and isinstance(sub.value, ast.Call):
                                cls.fields.setdefault(t.attr, _name_of(sub.value.func).split(".")[-1])
                            elif isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self" and isinstance(sub.value, ast.Name):
                                cls.fields.setdefault(t.attr, sub.value.id)
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    cls.fields[item.target.id] = _name_of(item.annotation).split(".")[-1]
                elif isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
                    # DRF: permission_classes = [IsAuthenticated]
                    cls.fields[item.targets[0].id] = _src(item.value)
    return tree


def _name_of(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _name_of(node.value) + "." + node.attr
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    if isinstance(node, ast.Subscript):
        return _name_of(node.value)
    if isinstance(node, ast.Constant):
        return repr(node.value)
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _src(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _params_text(node) -> str:
    try:
        return ast.unparse(node.args)
    except Exception:
        return ""


def _decorators_text(node) -> str:
    return "\n".join("@" + _src(d) for d in getattr(node, "decorator_list", []))


# ----------------------------------------------------------------------------- statement tree

def py_to_stmts(body: List[ast.stmt]) -> List[Stmt]:
    out: List[Stmt] = []
    for node in body:
        st = _convert(node)
        if st is not None:
            out.append(st)
    return out


def _convert(node: ast.stmt) -> Optional[Stmt]:
    line = getattr(node, "lineno", 0)
    if isinstance(node, ast.If):
        arms: List[Arm] = []
        cur: Optional[ast.stmt] = node
        first = True
        while isinstance(cur, ast.If):
            arms.append(Arm("if" if first else "else if", _src(cur.test), py_to_stmts(cur.body)))
            first = False
            if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
                cur = cur.orelse[0]
            else:
                if cur.orelse:
                    arms.append(Arm("else", "", py_to_stmts(cur.orelse)))
                cur = None
        return Stmt("if", "", line, cond=arms[0].cond, arms=arms)
    if isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
        arms = [Arm("try", "", py_to_stmts(node.body))]
        for h in node.handlers:
            exc = _src(h.type) if h.type is not None else ""
            arms.append(Arm("catch " + exc if exc else "catch", exc, py_to_stmts(h.body)))
        if node.orelse:
            arms.append(Arm("else", "", py_to_stmts(node.orelse)))
        if node.finalbody:
            arms.append(Arm("finally", "", py_to_stmts(node.finalbody)))
        return Stmt("try", "", line, arms=arms)
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return Stmt("loop", "for", line, cond=f"{_src(node.target)} in {_src(node.iter)}", body=py_to_stmts(node.body))
    if isinstance(node, ast.While):
        return Stmt("loop", "while", line, cond=_src(node.test), body=py_to_stmts(node.body))
    if isinstance(node, ast.Return):
        return Stmt("return", _src(node.value) if node.value is not None else "", line)
    if isinstance(node, ast.Raise):
        return Stmt("throw", _src(node.exc) if node.exc is not None else "", line)
    if isinstance(node, getattr(ast, "Match", ())):
        arms = []
        for case in node.cases:
            pat = _src(case.pattern)
            label = "default" if pat == "_" else "case " + pat
            arms.append(Arm(label, "" if pat == "_" else pat, py_to_stmts(case.body)))
        return Stmt("switch", _src(node.subject), line, cond=_src(node.subject), arms=arms)
    if isinstance(node, (ast.With, ast.AsyncWith)):
        ctx = ", ".join(_src(i.context_expr) + (f" as {_src(i.optional_vars)}" if i.optional_vars is not None else "") for i in node.items)
        st = Stmt("expr", "with " + ctx, line)
        st.lambdas = [py_to_stmts(node.body)]
        return st
    if isinstance(node, (ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Assert, ast.Delete)):
        text = _src(node)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            return None   # docstring
        return Stmt("expr", text, line)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom,
                         ast.Pass, ast.Break, ast.Continue, ast.Global, ast.Nonlocal)):
        return None
    return Stmt("expr", _src(node), line)


# ----------------------------------------------------------------------------- endpoints

def _deco_call(d) -> Tuple[str, List[ast.expr], Dict[str, ast.expr]]:
    if isinstance(d, ast.Call):
        return _name_of(d.func), list(d.args), {k.arg: k.value for k in d.keywords if k.arg}
    return _name_of(d), [], {}


def _const_str(node) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{" + _src(v.value) + "}" for v in node.values)
    return None


def _const_list(node) -> List[str]:
    if isinstance(node, (ast.List, ast.Tuple)):
        return [str(_const_str(e) or _name_of(e)) for e in node.elts]
    s = _const_str(node)
    return [s] if s else []


def _kw_int(kw: Dict[str, ast.expr], key: str) -> Optional[int]:
    v = kw.get(key)
    if isinstance(v, ast.Constant) and isinstance(v.value, int):
        return v.value
    if v is not None:
        from ..httpcodes import status_from_expr
        return status_from_expr(_src(v))
    return None


class _PyFile:
    def __init__(self, sf, tree: ast.Module):
        self.sf = sf
        self.tree = tree
        self.router_prefix: Dict[str, str] = {}         # var -> declared prefix
        self.router_deps: Dict[str, List[str]] = {}     # var -> dependencies (auth)
        self.router_vars: set = set()
        self.includes: List[Tuple[str, str, str, int]] = []   # (app_var, prefix, target expr, line)


def _scan_routers(pf: _PyFile) -> None:
    for node in ast.walk(pf.tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            fname = _name_of(node.value.func).split(".")[-1]
            var = node.targets[0].id
            kw = {k.arg: k.value for k in node.value.keywords if k.arg}
            if fname in ("Blueprint", "APIRouter", "Flask", "FastAPI", "Api", "Namespace", "Router", "Sanic", "Quart", "Starlette", "APIBlueprint", "DefaultRouter", "SimpleRouter"):
                pf.router_vars.add(var)
                prefix = _const_str(kw.get("url_prefix")) or _const_str(kw.get("prefix")) or ""
                if fname == "Namespace" and node.value.args:
                    prefix = _const_str(node.value.args[0]) or ""
                if prefix:
                    pf.router_prefix[var] = prefix
                deps = kw.get("dependencies")
                if deps is not None:
                    pf.router_deps[var] = [_src(d) for d in getattr(deps, "elts", [])]
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            fname = _name_of(call.func)
            short = fname.split(".")[-1]
            if short in ("register_blueprint", "include_router", "add_namespace", "mount", "blueprint") and call.args:
                app_var = fname.split(".")[0]
                kw = {k.arg: k.value for k in call.keywords if k.arg}
                prefix = _const_str(kw.get("url_prefix")) or _const_str(kw.get("prefix")) or ""
                if short == "mount" and call.args and _const_str(call.args[0]):
                    prefix = _const_str(call.args[0]) or ""
                    target = _src(call.args[1]) if len(call.args) > 1 else ""
                else:
                    target = _src(call.args[0])
                pf.includes.append((app_var, prefix, target, node.lineno))


def _resolve_router_target(index: CodeIndex, pf: _PyFile, expr: str, pfiles: Dict[str, _PyFile]) -> Optional[Tuple[str, str]]:
    """`orders.router` / `orders_bp` -> (file, var)"""
    parts = expr.split(".")
    if len(parts) == 1:
        if parts[0] in pf.router_vars:
            return (pf.sf.path, parts[0])
        imp = index.resolve_import(pf.sf.path, parts[0])
        if imp and imp[0]:
            tfile, exported = imp
            if exported == "*" and tfile in pfiles:
                rv = sorted(pfiles[tfile].router_vars)
                return (tfile, rv[0]) if rv else None
            return (tfile, exported)
        return None
    imp = index.resolve_import(pf.sf.path, parts[0])
    if imp and imp[0]:
        return (imp[0], parts[-1])
    return None


def extract(index: CodeIndex, files) -> List[EndpointSeed]:
    seeds: List[EndpointSeed] = []
    pfiles: Dict[str, _PyFile] = {}
    for sf in files:
        tree = getattr(sf, "_py_tree", None)
        if tree is None:
            continue
        pf = _PyFile(sf, tree)
        _scan_routers(pf)
        pfiles[sf.path] = pf

    # mount graph -> full prefixes
    edges: List[Tuple[Tuple[str, str], str, Tuple[str, str]]] = []
    for path, pf in pfiles.items():
        for app_var, prefix, target, _line in pf.includes:
            dst = _resolve_router_target(index, pf, target, pfiles)
            if dst:
                edges.append(((path, app_var), prefix, dst))
    full: Dict[Tuple[str, str], str] = {}
    targets = {e[2] for e in edges}

    def walk(node, prefix, seen):
        if node in seen:
            return
        seen = seen | {node}
        full.setdefault(node, prefix)
        for src, p, dst in edges:
            if src == node:
                own = pfiles[dst[0]].router_prefix.get(dst[1], "") if dst[0] in pfiles else ""
                walk(dst, join_paths(prefix, p, own) if (p or own or prefix) else "", seen)

    for path, pf in pfiles.items():
        for v in pf.router_vars:
            node = (path, v)
            if node not in targets:
                walk(node, pf.router_prefix.get(v, ""), set())

    for path, pf in pfiles.items():
        sf = pf.sf
        for node in ast.walk(pf.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seeds.extend(_function_endpoints(index, pf, node, full, owner=None))
            elif isinstance(node, ast.ClassDef):
                seeds.extend(_class_endpoints(index, pf, node, full, pfiles))
        seeds.extend(_django_urls(index, pf, pfiles))
    return seeds


def _function_endpoints(index: CodeIndex, pf: _PyFile, node, full, owner: Optional[ast.ClassDef]) -> List[EndpointSeed]:
    seeds: List[EndpointSeed] = []
    sf = pf.sf
    routes: List[Tuple[str, List[str], str, Dict[str, ast.expr]]] = []   # (router var, verbs, path, kwargs)
    props: Dict = {}
    auth: List[str] = []
    for d in node.decorator_list:
        name, args, kw = _deco_call(d)
        parts = name.split(".")
        short = parts[-1]
        var = parts[0] if len(parts) > 1 else ""
        if short == "route" and args:
            p = _const_str(args[0]) or ""
            verbs = _const_list(kw.get("methods")) if kw.get("methods") is not None else ["GET"]
            routes.append((var, [v.upper() for v in verbs], p, kw))
        elif short in VERB_DECOS and len(parts) > 1 and (args or var):
            p = _const_str(args[0]) if args else ""
            routes.append((var, [VERB_DECOS[short]], p or "", kw))
        elif short == "api_view":
            verbs = _const_list(args[0]) if args else ["GET"]
            routes.append(("", [v.upper() for v in verbs], "", kw))
            index.note_framework("django-rest")
        elif short == "action":
            verbs = _const_list(kw.get("methods")) if kw.get("methods") is not None else ["GET"]
            detail = isinstance(kw.get("detail"), ast.Constant) and kw["detail"].value is True
            url_path = _const_str(kw.get("url_path")) or node.name.replace("_", "-")
            routes.append(("__action__", [v.upper() for v in verbs], ("/{pk}/" if detail else "/") + url_path, kw))
        elif short in AUTH_DECOS or AUTH_DEP_RX.search(short) and short not in ("validate", "route"):
            auth.append("@" + name + ("(" + ", ".join(_src(a) for a in args) + ")" if args else ""))
        elif short in ("validate", "validate_request", "use_args", "use_kwargs", "expect", "validate_body", "marshal_with"):
            props.setdefault("validation", []).append("@" + name)
        elif short in ("cache", "cached", "cache_page", "memoize"):
            props.setdefault("cache", []).append("@" + name)
        elif short in ("limiter", "limit", "ratelimit", "throttle"):
            props.setdefault("rate_limit", []).append("@" + name)
    if not routes:
        return seeds
    fn = _fn_for(index, sf, node, owner.name if owner else "")
    # parameters (FastAPI style)
    path_params, query, headers, body = [], [], [], ""
    for arg in list(node.args.args) + list(node.args.kwonlyargs):
        if arg.arg in ("self", "cls", "request", "req", "response"):
            continue
        ann = _name_of(arg.annotation).split(".")[-1] if arg.annotation is not None else ""
        default = _default_for(node, arg.arg)
        dname = _name_of(default.func).split(".")[-1] if isinstance(default, ast.Call) else ""
        if dname in ("Depends", "Security"):
            dep = _src(default.args[0]) if default.args else ""
            if AUTH_DEP_RX.search(dep) or AUTH_DEP_RX.search(arg.arg):
                auth.append(f"Depends({dep})")
            else:
                props.setdefault("dependencies", []).append(dep)
        elif dname == "Query":
            query.append(arg.arg + ("" if _required(default) else "?"))
        elif dname == "Header":
            headers.append(arg.arg)
        elif dname == "Body":
            body = ann or "body"
        elif dname == "Path":
            path_params.append(arg.arg)
        elif dname == "Form" or dname == "File" or dname == "UploadFile":
            props.setdefault("form", []).append(arg.arg)
        elif ann and ann[0].isupper() and ann not in ("Request", "Response", "Optional", "List", "Dict", "Any", "Session", "AsyncSession", "BackgroundTasks", "Annotated") and ann not in ("int", "str", "float", "bool", "UUID"):
            body = ann
        elif ann and ann not in ("Request", "Response") and not body:
            pass
    for var, verbs, p, kw in routes:
        for m in re.finditer(r"\{(\w+)", p):
            if m.group(1) not in path_params:
                path_params.append(m.group(1))
        for m in re.finditer(r"<(?:\w+:)?(\w+)>", p):
            if m.group(1) not in path_params:
                path_params.append(m.group(1))
    for arg in node.args.args:
        if arg.arg in path_params:
            continue
        ann = _name_of(arg.annotation).split(".")[-1] if arg.annotation is not None else ""
        default = _default_for(node, arg.arg)
        if ann in ("int", "str", "float", "bool", "UUID") and not (isinstance(default, ast.Call)) and arg.arg not in query and arg.arg not in path_params:
            query.append(arg.arg + ("?" if default is not None else ""))
    if path_params:
        props["path_params"] = path_params
    if query:
        props["query_params"] = query
    if headers:
        props["headers"] = headers
    if body:
        props["body"] = body
        if body != "body":
            props.setdefault("validation", []).append(f"pydantic {body}")
    if node.returns is not None:
        props["returns"] = _src(node.returns)
    if isinstance(node, ast.AsyncFunctionDef):
        props["async"] = True
    cls = index.class_named(owner.name, sf.path) if owner else None
    for var, verbs, p, kw in routes:
        prefix = full.get((sf.path, var), pf.router_prefix.get(var, "")) if var else ""
        ep_props = dict(props)
        rauth = list(auth) + [d for d in pf.router_deps.get(var, []) if AUTH_DEP_RX.search(d)]
        rauth = list(dict.fromkeys(rauth))
        if rauth:
            ep_props["auth"] = rauth
        sc = _kw_int(kw, "status_code")
        if sc:
            ep_props["status"] = sc
        rm = kw.get("response_model")
        if rm is not None:
            ep_props["returns"] = _src(rm)
        if isinstance(kw.get("deprecated"), ast.Constant) and kw["deprecated"].value:
            ep_props["deprecated"] = True
        summ = _const_str(kw.get("summary")) or _const_str(kw.get("description"))
        if summ:
            ep_props["spec_summary"] = summ
        if kw.get("responses") is not None:
            ep_props["declared_responses"] = [int(k.value) for k in getattr(kw["responses"], "keys", []) if isinstance(k, ast.Constant) and isinstance(k.value, int)]
        framework = _framework_for(var, index, pf)
        for verb in verbs:
            ep = Endpoint(method=verb, path=join_paths(prefix, p) if var != "__action__" else p,
                          handler=Handler((owner.name + "." if owner else "") + node.name, sf.path, node.lineno, "python"),
                          framework=framework, properties=ep_props)
            seeds.append(EndpointSeed(endpoint=ep, fn=fn, owner_class=cls))
    return seeds


def _framework_for(var: str, index: CodeIndex, pf: _PyFile) -> str:
    t = pf.sf.text
    if "fastapi" in t.lower() or "APIRouter" in t:
        index.note_framework("fastapi")
        return "fastapi"
    if "flask" in t.lower():
        index.note_framework("flask")
        return "flask"
    if "django" in t.lower() or "rest_framework" in t:
        index.note_framework("django-rest")
        return "django-rest"
    index.note_framework("python")
    return "python"


def _required(default) -> bool:
    if isinstance(default, ast.Call) and default.args:
        a = default.args[0]
        return isinstance(a, ast.Constant) and a.value is Ellipsis
    return False


def _default_for(node, arg_name: str):
    args = node.args
    positional = list(args.args)
    defaults = list(args.defaults)
    offset = len(positional) - len(defaults)
    for i, a in enumerate(positional):
        if a.arg == arg_name:
            return defaults[i - offset] if i >= offset else None
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        if a.arg == arg_name:
            return d
    return None


def _fn_for(index: CodeIndex, sf, node, owner: str) -> Optional[FunctionDef]:
    for f in index.by_name.get(node.name, []):
        if f.file.path == sf.path and f.py_node is node:
            return f
    return None


def _class_endpoints(index: CodeIndex, pf: _PyFile, node: ast.ClassDef, full, pfiles) -> List[EndpointSeed]:
    seeds: List[EndpointSeed] = []
    bases = [_name_of(b).split(".")[-1] for b in node.bases]
    sf = pf.sf
    cls = index.class_named(node.name, sf.path)
    # @action / explicit route decorators on methods
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seeds.extend(_function_endpoints(index, pf, item, full, owner=node))
    if not any(b in ("MethodView", "Resource", "APIView", "GenericAPIView", "ViewSet", "ModelViewSet", "ReadOnlyModelViewSet",
                     "GenericViewSet", "View", "HTTPEndpoint", "HTTPMethodView", "ListCreateAPIView", "RetrieveUpdateDestroyAPIView",
                     "RetrieveAPIView", "ListAPIView", "CreateAPIView", "UpdateAPIView", "DestroyAPIView") for b in bases):
        return seeds
    # find where this class is registered: add_resource / as_view() / router.register
    registrations: List[Tuple[str, str]] = []    # (path, kind)
    for p, other in pfiles.items():
        for n in ast.walk(other.tree):
            if isinstance(n, ast.Call):
                fname = _name_of(n.func).split(".")[-1]
                if fname == "add_resource" and n.args and _name_of(n.args[0]).split(".")[-1] == node.name:
                    for a in n.args[1:]:
                        s = _const_str(a)
                        if s:
                            prefix = full.get((p, _name_of(n.func).split(".")[0]), "")
                            registrations.append((join_paths(prefix, s), "resource"))
                elif fname == "add_url_rule" and len(n.args) >= 2 and node.name in _src(n.args[1]) + _src(n) and _const_str(n.args[0]):
                    if node.name in _src(n):
                        registrations.append((_const_str(n.args[0]) or "", "view"))
                elif fname == "register" and len(n.args) >= 2 and _name_of(n.args[1]).split(".")[-1] == node.name:
                    s = _const_str(n.args[0]) or ""
                    registrations.append(("/" + s.strip("/^$"), "viewset"))
                elif fname in ("path", "re_path", "url") and len(n.args) >= 2 and node.name in _src(n.args[1]):
                    s = _const_str(n.args[0]) or ""
                    registrations.append((_django_path(s), "view"))
    if not registrations:
        return seeds
    auth: List[str] = []
    if cls:
        for key in ("permission_classes", "authentication_classes", "decorators"):
            if key in cls.fields:
                auth.append(f"{key} = {cls.fields[key]}")
    for base_path, kind in registrations:
        if kind == "viewset":
            for method_name, (verb, suffix) in DRF_ACTIONS.items():
                fn = cls.methods.get(method_name) if cls else None
                implemented = fn is not None
                if not implemented and not any(b in ("ModelViewSet", "ReadOnlyModelViewSet") for b in bases):
                    continue
                if not implemented and any(b == "ReadOnlyModelViewSet" for b in bases) and verb not in ("GET",):
                    continue
                props: Dict = {"auth": list(auth)} if auth else {}
                if suffix:
                    props["path_params"] = ["pk"]
                if not implemented:
                    props["inherited"] = True
                ep = Endpoint(method=verb, path=join_paths(base_path, suffix), handler=Handler(f"{node.name}.{method_name}", sf.path, fn.line if fn else node.lineno, "python"),
                              framework="django-rest", properties=props)
                seeds.append(EndpointSeed(endpoint=ep, fn=fn, owner_class=cls))
            # actions registered on the viewset
            for s in seeds:
                if s.endpoint.handler.name.startswith(node.name + ".") and s.endpoint.path.startswith("/{pk}") or s.endpoint.path.startswith("/") and s.owner_class is cls and s.endpoint.framework in ("django-rest",) and s.endpoint.path.count("/") <= 2 and not s.endpoint.path.startswith(base_path):
                    if s.endpoint.method in ("GET", "POST", "PUT", "PATCH", "DELETE") and s.endpoint.path not in (join_paths(base_path, sfx) for _, sfx in DRF_ACTIONS.values()):
                        s.endpoint.path = join_paths(base_path, s.endpoint.path)
            continue
        for verb in ("get", "post", "put", "patch", "delete", "head", "options"):
            fn = cls.methods.get(verb) if cls else None
            if fn is None:
                continue
            props = {"auth": list(auth)} if auth else {}
            pp = re.findall(r"\{(\w+)\}", base_path)
            if pp:
                props["path_params"] = pp
            ep = Endpoint(method=verb.upper(), path=base_path, handler=Handler(f"{node.name}.{verb}", sf.path, fn.line, "python"),
                          framework="flask" if kind == "resource" or "MethodView" in bases else "django-rest", properties=props)
            seeds.append(EndpointSeed(endpoint=ep, fn=fn, owner_class=cls))
    return seeds


def _django_path(s: str) -> str:
    s = s.strip("^$")
    s = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", s)
    s = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", s)
    return "/" + s.strip("/")


def _django_urls(index: CodeIndex, pf: _PyFile, pfiles) -> List[EndpointSeed]:
    """urls.py: path('orders/<int:pk>/', views.order_detail) -> endpoints for function views."""
    seeds: List[EndpointSeed] = []
    if not pf.sf.path.endswith("urls.py"):
        return seeds
    prefix = ""
    # include() prefix from parent urls.py
    for p, other in pfiles.items():
        if not p.endswith("urls.py") or p == pf.sf.path:
            continue
        for n in ast.walk(other.tree):
            if isinstance(n, ast.Call) and _name_of(n.func).split(".")[-1] in ("path", "re_path", "url") and len(n.args) >= 2:
                inc = n.args[1]
                if isinstance(inc, ast.Call) and _name_of(inc.func).split(".")[-1] == "include" and inc.args:
                    mod = _const_str(inc.args[0]) or ""
                    if mod and _module_name(pf.sf.path).endswith(mod) or (mod and mod.replace(".", "/") + ".py" == pf.sf.path):
                        prefix = _django_path(_const_str(n.args[0]) or "")
    for n in ast.walk(pf.tree):
        if not (isinstance(n, ast.Call) and _name_of(n.func).split(".")[-1] in ("path", "re_path", "url") and len(n.args) >= 2):
            continue
        route = _const_str(n.args[0])
        if route is None:
            continue
        target = n.args[1]
        if isinstance(target, ast.Call):
            continue   # as_view()/include handled elsewhere
        tname = _name_of(target)
        parts = tname.split(".")
        fn = None
        if len(parts) >= 2:
            imp = index.resolve_import(pf.sf.path, parts[0])
            if imp and imp[0]:
                fn = index.function_in_file(imp[0], parts[-1])
        else:
            fn = index.function_in_file(pf.sf.path, parts[0])
            if fn is None:
                imp = index.resolve_import(pf.sf.path, parts[0])
                if imp and imp[0]:
                    fn = index.function_in_file(imp[0], imp[1])
        if fn is None:
            continue
        # skip if that function already produced endpoints through @api_view
        if any(isinstance(d, ast.Call) and _name_of(d.func).split(".")[-1] == "api_view" for d in getattr(fn.py_node, "decorator_list", [])):
            verbs = []
            for d in fn.py_node.decorator_list:
                if isinstance(d, ast.Call) and _name_of(d.func).split(".")[-1] == "api_view":
                    verbs = _const_list(d.args[0]) if d.args else ["GET"]
        else:
            verbs = sorted(set(re.findall(r"request\.method\s*(?:==|in)\s*[\[(]?\s*['\"](\w+)['\"]", fn.file.text))) or ["ANY"]
        full_path = join_paths(prefix, _django_path(route))
        pp = re.findall(r"\{(\w+)\}", full_path)
        auth = ["@" + _src(d) for d in getattr(fn.py_node, "decorator_list", []) if _name_of(d).split(".")[-1] in AUTH_DECOS]
        props: Dict = {}
        if pp:
            props["path_params"] = pp
        if auth:
            props["auth"] = auth
        index.note_framework("django")
        for v in verbs:
            ep = Endpoint(method=v.upper(), path=full_path, handler=Handler(fn.qualname, fn.file.path, fn.line, "python"), framework="django", properties=dict(props))
            seeds.append(EndpointSeed(endpoint=ep, fn=fn))
    return seeds
