"""Endpoints for Java/Kotlin: Spring MVC / WebFlux annotations and JAX-RS.

Also indexes two things the tracer uses for labelling:
- `@FeignClient` / Retrofit interfaces  -> outbound HTTP calls with a service name
- Spring Data repositories               -> database access with an entity name
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .. import lexer
from ..httpcodes import status_from_name
from ..model import Endpoint, Handler, join_paths
from .annotations import as_list, find_annotations, parse_args
from .base import ClassDef, CodeIndex, EndpointSeed, FunctionDef, parse_params

MAPPING = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE",
           "PatchMapping": "PATCH", "RequestMapping": None}
JAXRS_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
CONTROLLER_MARKERS = {"RestController", "Controller", "RequestMapping", "Path", "RestControllerAdvice"}
REPO_BASES = {"JpaRepository", "CrudRepository", "PagingAndSortingRepository", "MongoRepository", "ReactiveCrudRepository",
              "ReactiveMongoRepository", "R2dbcRepository", "ElasticsearchRepository", "Repository", "JpaSpecificationExecutor",
              "CoroutineCrudRepository", "DynamoDBCrudRepository", "CassandraRepository", "Neo4jRepository", "ListCrudRepository"}
RESILIENCE = {"CircuitBreaker", "Retry", "RateLimiter", "Bulkhead", "TimeLimiter", "Timeout", "Retryable"}
ASYNC_TYPES = ("CompletableFuture", "Mono", "Flux", "DeferredResult", "Callable", "Deferred", "Flow")


def _paths_from(args: Dict) -> List[str]:
    paths = as_list(args.get("value")) + as_list(args.get("path"))
    paths = [p for p in paths if isinstance(p, str)]
    return paths or [""]


def _auth_from(anns: List) -> List[str]:
    out: List[str] = []
    for name, args in anns:
        a = parse_args(args)
        if name == "PreAuthorize" or name == "PostAuthorize":
            out.append(str(a.get("value", "")))
        elif name in ("Secured", "RolesAllowed"):
            out.extend(str(x) for x in as_list(a.get("value")))
        elif name == "PermitAll":
            out.append("permitAll")
        elif name == "DenyAll":
            out.append("denyAll")
        elif name == "Authenticated":
            out.append("authenticated")
    return [o for o in out if o]


def index_java_extras(index: CodeIndex, files) -> None:
    """Record Feign/Retrofit clients and Spring Data repositories."""
    index.http_clients = getattr(index, "http_clients", {})
    index.repositories = getattr(index, "repositories", {})
    for sf in files:
        for blocks in [index.blocks.get(sf.path, [])]:
            for b in blocks:
                if b.kind != "class":
                    continue
                anns = find_annotations(b.sig)
                names = {n for n, _ in anns}
                if "FeignClient" in names or "HttpExchange" in names or "RetrofitClient" in names:
                    args = {}
                    for n, a in anns:
                        if n in ("FeignClient", "HttpExchange"):
                            args = parse_args(a)
                    service = str(args.get("name") or args.get("value") or args.get("url") or b.name)
                    methods: Dict[str, str] = {}
                    cls = index.class_named(b.name, sf.path)
                    body = sf.text[b.open + 1:b.close]
                    for m in re.finditer(r"((?:@[\w.]+(?:\([^)]*\))?\s*)+)[^;{@]*?\b([A-Za-z_]\w*)\s*\(", body):
                        ann_txt, mname = m.group(1), m.group(2)
                        for n, a in find_annotations(ann_txt):
                            verb = MAPPING.get(n) or (n.replace("Exchange", "").upper() if n.endswith("Exchange") else None) or (n if n in JAXRS_METHODS else None)
                            if verb or n == "RequestMapping":
                                pa = parse_args(a)
                                p = _paths_from(pa)[0]
                                v = verb or str(pa.get("method", "ANY"))
                                methods[mname] = f"{v} {p}".strip()
                    index.http_clients[b.name] = {"service": service, "methods": methods}
                    if cls:
                        index.note_framework("feign")
                for base in b.extends:
                    if base in REPO_BASES:
                        m = re.search(r"%s\s*<\s*([\w.]+)" % re.escape(base), b.sig)
                        entity = m.group(1).split(".")[-1] if m else ""
                        index.repositories[b.name] = entity
                        index.note_framework("spring-data")
                        break
                if b.class_kind == "interface" and b.name.endswith(("Repository", "Dao", "Repo")) and b.name not in index.repositories:
                    index.repositories[b.name] = ""


def _blank(masked: str, src: str, start: int, end: int) -> str:
    from .base import _blank_nested_blocks, _text_by_mask
    return _text_by_mask(src, _blank_nested_blocks(masked, start, end), start)


def extract(index: CodeIndex, files) -> List[EndpointSeed]:
    seeds: List[EndpointSeed] = []
    for sf in files:
        blocks = index.blocks.get(sf.path, [])
        for b in blocks:
            if b.kind != "class":
                continue
            class_anns = find_annotations(b.sig)
            names = {n for n, _ in class_anns}
            if not (names & CONTROLLER_MARKERS):
                continue
            if "RestControllerAdvice" in names or "ControllerAdvice" in names:
                _index_exception_handlers(index, sf, b)
                continue
            is_jaxrs = "Path" in names and not (names & {"RestController", "Controller", "RequestMapping"})
            base_paths = [""]
            for n, a in class_anns:
                if n == "RequestMapping":
                    base_paths = _paths_from(parse_args(a))
                elif n == "Path":
                    base_paths = [str(parse_args(a).get("value", ""))]
            class_auth = _auth_from(class_anns)
            cls = index.class_named(b.name, sf.path)
            index.note_framework("jax-rs" if is_jaxrs else "spring")
            for fb in blocks:
                if fb.kind != "function" or fb.receiver != b.name or fb.parent is not b:
                    continue
                fn = _find_fn(index, cls, fb.name, sf.path, fb.line)
                anns = find_annotations(fb.sig)
                if not anns:
                    continue
                verbs: List[str] = []
                paths: List[str] = [""]
                mapping_args: Dict = {}
                for n, a in anns:
                    if n in MAPPING:
                        mapping_args = parse_args(a)
                        paths = _paths_from(mapping_args)
                        if MAPPING[n]:
                            verbs.append(MAPPING[n])
                        else:
                            ms = [str(x) for x in as_list(mapping_args.get("method"))]
                            verbs.extend(ms or ["ANY"])
                    elif n in JAXRS_METHODS:
                        verbs.append(n)
                    elif n == "Path" and n != "RequestMapping":
                        paths = [str(parse_args(a).get("value", ""))]
                if not verbs:
                    continue
                mapping_args = dict(mapping_args)
                mapping_args["_verb"] = verbs[0].upper()
                props = _method_properties(anns, fb, index, class_auth, mapping_args, sf.lang)
                for base in base_paths:
                    for p in paths:
                        for verb in verbs:
                            ep = Endpoint(method=verb, path=join_paths(base, p),
                                          handler=Handler(f"{b.name}.{fb.name}", sf.path, fb.line, sf.lang),
                                          framework="jax-rs" if is_jaxrs else "spring",
                                          properties=dict(props))
                            seeds.append(EndpointSeed(endpoint=ep, fn=fn, owner_class=cls))
    return seeds


def _find_fn(index: CodeIndex, cls: Optional[ClassDef], name: str, path: str, line: int) -> Optional[FunctionDef]:
    for f in index.by_name.get(name, []):
        if f.file.path == path and f.line == line:
            return f
    if cls:
        return cls.methods.get(name)
    return None


_NON_BODY_TYPES = {"HttpServletRequest", "HttpServletResponse", "ServerWebExchange", "ServerHttpRequest", "ServerHttpResponse", "Model", "ModelMap",
                   "ModelAndView", "BindingResult", "Errors", "RedirectAttributes", "HttpSession", "WebRequest", "NativeWebRequest", "UriComponentsBuilder",
                   "Locale", "TimeZone", "ZoneId", "Principal", "Authentication", "SecurityContext", "UserDetails", "JwtAuthenticationToken",
                   "OAuth2AuthenticationToken", "Pageable", "Sort", "SessionStatus", "HttpHeaders", "HttpMethod", "HttpEntity", "InputStream",
                   "OutputStream", "Reader", "Writer", "String", "Long", "Integer", "int", "long", "UUID", "Boolean", "boolean", "Double", "double",
                   "MultipartFile", "Part", "SseEmitter", "WebSession", "Continuation", "ServerRequest", "Request", "Response", "UriInfo", "HttpHeaders", "SecurityContextHolder"}


def _method_properties(anns, fb, index: CodeIndex, class_auth: List[str], mapping_args: Dict, lang: str) -> Dict:
    props: Dict = {}
    path_params, query_params, headers, validation = [], [], [], []
    body = ""
    for ann_txt, ptype, pname in parse_params(lang, fb.params):
        panns = find_annotations(ann_txt)
        pnames = {n for n, _ in panns}
        short_type = re.sub(r"<.*", "", ptype).split(".")[-1]
        for n, a in panns:
            pa = parse_args(a)
            explicit = pa.get("value") or pa.get("name")
            if n == "PathVariable" or n == "PathParam":
                path_params.append(str(explicit or pname))
            elif n in ("RequestParam", "QueryParam"):
                q = str(explicit or pname)
                if pa.get("required") is False or ptype.endswith("?") or "Optional" in ptype:
                    q += "?"
                query_params.append(q)
            elif n in ("RequestHeader", "HeaderParam"):
                headers.append(str(explicit or pname))
            elif n == "RequestBody":
                body = short_type
            elif n in ("Valid", "Validated"):
                validation.append(f"@{n} {short_type}")
                if not body and mapping_args.get("_verb") in ("POST", "PUT", "PATCH") and short_type and short_type[0].isupper() and short_type not in _NON_BODY_TYPES:
                    body = short_type
            elif n in ("AuthenticationPrincipal", "CurrentUser", "Context"):
                props.setdefault("auth_context", []).append(pname)
        if short_type in ("Principal", "Authentication", "JwtAuthenticationToken", "OAuth2AuthenticationToken", "SecurityContext", "UserDetails"):
            props.setdefault("auth_context", []).append(f"{short_type} {pname}")
        if not pnames and short_type and short_type not in _NON_BODY_TYPES and fb.params and not body and mapping_args.get("_verb") in ("POST", "PUT", "PATCH"):
            # JAX-RS style body param (unannotated complex type) on a mutating verb
            if short_type[0].isupper():
                body = short_type
        if "ModelAttribute" in pnames and mapping_args.get("_verb") in ("POST", "PUT", "PATCH") and not body:
            body = short_type
    if path_params:
        props["path_params"] = path_params
    if query_params:
        props["query_params"] = query_params
    if headers:
        props["headers"] = headers
    if body:
        props["body"] = body
    if validation:
        props["validation"] = validation
    auth = class_auth + _auth_from(anns)
    if auth:
        props["auth"] = auth
    for n, a in anns:
        pa = parse_args(a)
        if n == "ResponseStatus":
            code = status_from_name(str(pa.get("value") or pa.get("code") or ""))
            if code:
                props["status"] = code
        elif n == "Deprecated":
            props["deprecated"] = True
        elif n in RESILIENCE:
            props.setdefault("resilience", []).append(n + (f"({pa.get('name')})" if pa.get("name") else ""))
        elif n == "Transactional":
            props["transactional"] = True
        elif n in ("Cacheable", "CachePut", "CacheEvict"):
            props.setdefault("cache", []).append(n + (f"({pa.get('value') or pa.get('cacheNames')})" if (pa.get('value') or pa.get('cacheNames')) else ""))
        elif n == "Async":
            props["async"] = True
        elif n in ("Produces", "Consumes"):
            props[n.lower()] = [str(x) for x in as_list(pa.get("value"))]
        elif n in ("Operation", "ApiOperation"):
            s = pa.get("summary") or pa.get("value")
            if s:
                props["spec_summary"] = str(s)
    for key in ("consumes", "produces"):
        if mapping_args.get(key):
            props[key] = [str(x) for x in as_list(mapping_args.get(key))]
    ret = _return_type(fb.sig, lang)
    if ret:
        props["returns"] = ret
        if any(ret.startswith(t) for t in ASYNC_TYPES):
            props["async"] = True
    return props


def _return_type(sig: str, lang: str) -> str:
    from .. import lexer as _lx
    s = _lx._strip_annotations(sig, lang)
    s = re.sub(r"\s+", " ", s).strip()
    if lang == "kotlin":
        m = re.search(r"\)\s*:\s*([\w.<>?, ]+)\s*$", s)
        return m.group(1).strip() if m else ""
    m = re.search(r"([\w.<>\[\]?, ]+?)\s+\w+\s*\(", s)
    if not m:
        return ""
    t = m.group(1).strip()
    t = re.sub(r"^(?:(?:public|private|protected|static|final|synchronized|abstract|default|override|suspend|async)\s+)+", "", t)
    return t.split(".")[-1] if "<" not in t else t


def _index_exception_handlers(index: CodeIndex, sf, class_block) -> None:
    """@ControllerAdvice: map handled exception classes to the status they produce."""
    for fb in index.blocks.get(sf.path, []):
        if fb.kind != "function" or fb.parent is not class_block:
            continue
        anns = find_annotations(fb.sig)
        excs: List[str] = []
        status: Optional[int] = None
        for n, a in anns:
            pa = parse_args(a)
            if n == "ExceptionHandler":
                excs = [str(x).replace(".class", "").replace("::class", "").split(".")[-1] for x in as_list(pa.get("value"))]
            elif n == "ResponseStatus":
                status = status_from_name(str(pa.get("value") or pa.get("code") or ""))
        if not excs:
            for _, t, _n in parse_params(sf.lang, fb.params):
                if "Exception" in t or "Error" in t:
                    excs.append(re.sub(r"<.*", "", t).split(".")[-1])
        if status is None:
            body = sf.text[fb.open:fb.close]
            m = re.search(r"HttpStatus\.([A-Z_]+)|status\(\s*(\d{3})\s*\)|\.(notFound|badRequest|ok|noContent|unprocessableEntity|internalServerError|accepted|created)\s*\(", body)
            if m:
                status = status_from_name(m.group(1) or m.group(2) or m.group(3) or "")
        if status:
            for e in excs:
                index.exception_status.setdefault(e, status)
