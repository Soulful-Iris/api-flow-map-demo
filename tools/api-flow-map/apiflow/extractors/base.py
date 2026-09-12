"""Shared types for extractors plus the brace-language code indexer.

The code index answers one question for the tracer: "this handler calls
`orderService.validate(order)` — where is that, and can I read its body?"
It maps class names to fields (variable -> type), files to imports, and every
function to a body range that the tracer can parse on demand.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .. import lexer
from ..discovery import Repo, SourceFile
from ..model import Endpoint


@dataclass
class FunctionDef:
    name: str
    file: SourceFile
    owner: str = ""                 # class / struct / receiver type name ("" for free functions)
    start: int = 0                  # body start index (after '{'), or python node body start
    end: int = 0                    # body end index (index of '}'), exclusive
    line: int = 0
    params: str = ""
    sig: str = ""                   # raw signature text incl. annotations/decorators
    has_body: bool = True
    package: str = ""               # go package / python module
    py_node: object = None          # python ast node (python only)
    is_handler_like: bool = False

    @property
    def qualname(self) -> str:
        return f"{self.owner}.{self.name}" if self.owner else self.name

    @property
    def lang(self) -> str:
        return self.file.lang


@dataclass
class ClassDef:
    name: str
    file: SourceFile
    kind: str = "class"             # class | interface | enum | record | struct | object
    extends: List[str] = field(default_factory=list)
    fields: Dict[str, str] = field(default_factory=dict)     # var -> Type
    methods: Dict[str, FunctionDef] = field(default_factory=dict)
    sig: str = ""
    start: int = 0
    end: int = 0
    line: int = 0


@dataclass
class EndpointSeed:
    """An endpoint plus what the tracer needs to expand it."""
    endpoint: Endpoint
    fn: Optional[FunctionDef] = None                   # handler function (may be synthetic/inline)
    extra_fns: List[FunctionDef] = field(default_factory=list)   # e.g. class-based views: same fn, different verbs
    owner_class: Optional[ClassDef] = None


class CodeIndex:
    def __init__(self, repo: Repo):
        self.repo = repo
        self.functions: List[FunctionDef] = []
        self.classes: Dict[str, List[ClassDef]] = {}          # simple name -> defs (may collide across packages)
        self.by_name: Dict[str, List[FunctionDef]] = {}       # method/function name -> defs
        self.by_file_name: Dict[Tuple[str, str], FunctionDef] = {}   # (file path, name) -> def (free functions)
        self.impls: Dict[str, List[str]] = {}                 # interface name -> implementing class names
        self.imports: Dict[str, Dict[str, Tuple[Optional[str], str]]] = {}  # file -> local name -> (file path|None, exported name)
        self.go_packages: Dict[str, List[FunctionDef]] = {}   # package name -> free functions
        self.exception_status: Dict[str, int] = {}            # exception class -> http status
        self.module_vars: Dict[str, Dict[str, str]] = {}      # file -> var name -> type/ctor hint
        self.frameworks: Dict[str, int] = {}
        self.blocks: Dict[str, List[lexer.Block]] = {}        # file -> scanned blocks (brace languages)
        self.http_clients: Dict[str, Dict] = {}               # Feign/Retrofit interface -> {"service", "methods"}
        self.repositories: Dict[str, str] = {}                # repository class -> entity name
        self.exports: Dict[str, Dict[str, str]] = {}          # JS file -> exported name -> local name
        self.constants: Dict[str, Dict[str, str]] = {}        # file -> CONSTANT -> string value

    # ------------------------------------------------------------------ registration
    def add_function(self, fn: FunctionDef) -> None:
        self.functions.append(fn)
        self.by_name.setdefault(fn.name, []).append(fn)
        if not fn.owner:
            self.by_file_name[(fn.file.path, fn.name)] = fn
        if fn.package:
            self.go_packages.setdefault(fn.package, []).append(fn)

    def add_class(self, cls: ClassDef) -> None:
        self.classes.setdefault(cls.name, []).append(cls)
        for base in cls.extends:
            self.impls.setdefault(base, []).append(cls.name)

    def note_framework(self, name: str) -> None:
        self.frameworks[name] = self.frameworks.get(name, 0) + 1

    # ------------------------------------------------------------------ lookup
    def class_named(self, name: str, prefer_file: Optional[str] = None) -> Optional[ClassDef]:
        cands = self.classes.get(name)
        if not cands:
            return None
        if prefer_file:
            for c in cands:
                if c.file.path == prefer_file:
                    return c
            # same directory
            d = os.path.dirname(prefer_file)
            for c in cands:
                if os.path.dirname(c.file.path) == d:
                    return c
        return cands[0]

    def method_of(self, class_name: str, method: str, prefer_file: Optional[str] = None, _seen=None) -> Optional[FunctionDef]:
        """Find `Class.method` following implementations of interfaces and
        superclasses. Returns a def with a body when one exists."""
        _seen = _seen or set()
        if class_name in _seen:
            return None
        _seen.add(class_name)
        cls = self.class_named(class_name, prefer_file)
        fallback: Optional[FunctionDef] = None
        if cls:
            fn = cls.methods.get(method)
            if fn and fn.has_body:
                return fn
            fallback = fn
            for base in cls.extends:
                found = self.method_of(base, method, prefer_file, _seen)
                if found:
                    return found
        # implementations (interface -> impl, abstract -> concrete)
        for impl in self.impls.get(class_name, []):
            found = self.method_of(impl, method, prefer_file, _seen)
            if found:
                return found
        # naming conventions: OrderService -> OrderServiceImpl / DefaultOrderService
        for cand in (class_name + "Impl", "Default" + class_name, class_name.lstrip("I") if class_name.startswith("I") and len(class_name) > 1 and class_name[1].isupper() else ""):
            if cand and cand != class_name and cand in self.classes:
                found = self.method_of(cand, method, prefer_file, _seen)
                if found:
                    return found
        return fallback

    def unique_function(self, name: str, prefer_file: Optional[str] = None) -> Optional[FunctionDef]:
        cands = [f for f in self.by_name.get(name, []) if f.has_body]
        if not cands:
            return None
        if prefer_file:
            same = [f for f in cands if f.file.path == prefer_file]
            if len(same) == 1:
                return same[0]
        if len(cands) == 1:
            return cands[0]
        owners = {f.owner for f in cands}
        if len(owners) == 1 and len(cands) <= 2:
            return cands[0]
        return None

    def function_in_file(self, path: str, name: str) -> Optional[FunctionDef]:
        fn = self.by_file_name.get((path, name))
        if fn:
            return fn
        # class-based module: any method with that name in the file
        for f in self.by_name.get(name, []):
            if f.file.path == path and f.has_body:
                return f
        return None

    def resolve_import(self, file_path: str, local_name: str) -> Optional[Tuple[Optional[str], str]]:
        return self.imports.get(file_path, {}).get(local_name)


# ---------------------------------------------------------------------------- brace indexer

_JAVA_FIELD_RX = re.compile(
    r"(?:^|[;{}\n])\s*(?:@[\w.]+(?:\([^)]*\))?\s*)*(?:(?:public|private|protected|static|final|readonly|volatile|transient|lateinit|override)\s+)*"
    r"([A-Z][\w.]*(?:<[^;=]*?>)?(?:\[\])?)\s+([a-z_$][\w$]*)\s*(?:=|;)", re.M)
_KOTLIN_PROP_RX = re.compile(r"(?:val|var)\s+([a-z_][\w]*)\s*:\s*([A-Z][\w.<>?, ]*)")
_TS_PROP_RX = re.compile(
    r"(?:^|[;{}\n])\s*(?:(?:public|private|protected|readonly|static|declare|override)\s+)*([a-z_$#][\w$]*)\s*[?!]?\s*:\s*([A-Z][\w.<>|\[\] ]*)", re.M)
_TS_CTOR_PARAM_RX = re.compile(r"(?:public|private|protected|readonly)\s+(?:readonly\s+)?([a-z_$][\w$]*)\s*:\s*([A-Z][\w.<>]*)")
_THIS_ASSIGN_RX = re.compile(r"this\.([A-Za-z_$][\w$]*)\s*=\s*(?:new\s+)?([A-Za-z_$][\w$.]*)")
_GO_STRUCT_FIELD_RX = re.compile(r"^\s*([A-Za-z_]\w*)\s+\*?([\w.]+)", re.M)
_GO_PACKAGE_RX = re.compile(r"^\s*package\s+(\w+)", re.M)
_GO_VAR_TYPE_RX = re.compile(r"\b([A-Za-z_]\w*)\s*:?=\s*&?(?:[\w]+\.)?([A-Z]\w*)\{|\b([A-Za-z_]\w*)\s*(?:,\s*\w+\s*)?:?=\s*(?:[\w]+\.)?(New[A-Z]\w*)\((?:[^()]|\([^()]*\))*\)(?!\s*\.)")
_JAVA_PARAM_RX = re.compile(r"(?:@[\w.]+(?:\([^)]*\))?\s*)*(?:final\s+)?([A-Z][\w.]*(?:<[^,]*?>)?)\s+([a-z_$][\w$]*)")
_KOTLIN_PARAM_RX = re.compile(r"(?:(?:private|public|protected|internal)\s+)?(?:val|var)?\s*([a-z_][\w]*)\s*:\s*([A-Z][\w.<>?]*)")
_CS_PARAM_RX = re.compile(r"(?:\[[^\]]*\]\s*)*(?:this\s+)?([A-Z][\w.]*(?:<[^,]*?>)?\??)\s+([a-z_@][\w]*)")

_JS_REQUIRE_RX = re.compile(r"(?:const|let|var)\s+(\{[^}]*\}|[A-Za-z_$][\w$]*)\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)(?:\.([A-Za-z_$][\w$]*))?")
_JS_IMPORT_RX = re.compile(r"import\s+(?:type\s+)?(.+?)\s+from\s+['\"]([^'\"]+)['\"]", re.S)
_JS_EXPORTS_ASSIGN_RX = re.compile(r"module\.exports\s*=\s*([A-Za-z_$][\w$]*)\s*;?")
_JS_EXPORT_DEFAULT_RX = re.compile(r"export\s+default\s+([A-Za-z_$][\w$]*)\s*;?")

_RESPONSE_STATUS_RX = re.compile(r"@ResponseStatus\s*\(\s*(?:value\s*=\s*|code\s*=\s*)?(?:HttpStatus\.)?([A-Z_]+)")

_MASK_INNER_BLOCKS_LANGS = ("java", "kotlin", "csharp", "typescript", "javascript", "go")


def _blank_nested_blocks(masked: str, start: int, end: int) -> str:
    """Return masked[start:end] with the interiors of nested { } blanked so
    member-level regexes only see declarations."""
    seg = list(masked[start:end])
    depth = 0
    for i, c in enumerate(seg):
        if c == "{":
            depth += 1
            if depth >= 1:
                seg[i] = "{"
        elif c == "}":
            if depth >= 1:
                seg[i] = "}"
            depth -= 1
        elif depth >= 1 and c != "\n":
            seg[i] = " "
    return "".join(seg)


def _text_by_mask(src: str, masked_seg: str, offset: int) -> str:
    """Map a blanked masked segment back to source characters where visible."""
    out = []
    for i, c in enumerate(masked_seg):
        out.append(src[offset + i] if c != " " else " ")
    return "".join(out)


def index_brace_file(sf: SourceFile, index: CodeIndex) -> List[lexer.Block]:
    """Index classes/functions/fields/imports of a Java/Kotlin/C#/JS/TS/Go file.
    Returns the raw blocks so framework extractors can reuse them."""
    lang = sf.lang
    masked = sf.masked
    blocks = lexer.scan_blocks(masked, sf.text, lang, sf.lines)
    package = ""
    if lang == "go":
        m = _GO_PACKAGE_RX.search(sf.text)
        package = m.group(1) if m else os.path.basename(os.path.dirname(sf.path)) or "main"

    classes_by_block: Dict[int, ClassDef] = {}
    for b in blocks:
        if b.kind == "class":
            cls = ClassDef(name=b.name, file=sf, kind=b.class_kind, extends=list(b.extends), sig=b.sig,
                           start=b.open + 1, end=b.close, line=b.line)
            body_masked = _blank_nested_blocks(masked, b.open + 1, b.close)
            body_src = _text_by_mask(sf.text, body_masked, b.open + 1)
            if lang in ("java", "csharp"):
                for m in _JAVA_FIELD_RX.finditer(body_src):
                    t = re.sub(r"<.*", "", m.group(1)).split(".")[-1]
                    cls.fields[m.group(2)] = t
                # constructor-injected fields are declared as fields anyway
            elif lang == "kotlin":
                for m in _KOTLIN_PROP_RX.finditer(b.sig + "\n" + body_src):
                    cls.fields[m.group(1)] = re.sub(r"[<?].*", "", m.group(2)).split(".")[-1]
            elif lang in ("typescript", "javascript"):
                for m in _TS_PROP_RX.finditer(body_src):
                    cls.fields[m.group(1).lstrip("#")] = re.sub(r"[<|\[].*", "", m.group(2)).split(".")[-1]
                for m in _TS_CTOR_PARAM_RX.finditer(sf.text[b.open:b.close]):
                    cls.fields[m.group(1)] = re.sub(r"<.*", "", m.group(2)).split(".")[-1]
                for m in _THIS_ASSIGN_RX.finditer(sf.text[b.open:b.close]):
                    cls.fields.setdefault(m.group(1), m.group(2).split(".")[-1])
            elif lang == "go" and b.class_kind == "struct":
                for m in _GO_STRUCT_FIELD_RX.finditer(body_src):
                    if m.group(1) not in ("type", "func", "var", "const"):
                        cls.fields[m.group(1)] = m.group(2).split(".")[-1]
            if lang in ("java", "kotlin") and cls.extends and any(e.endswith("Exception") for e in cls.extends):
                m = _RESPONSE_STATUS_RX.search(b.sig)
                if m:
                    from ..httpcodes import status_from_name
                    code = status_from_name(m.group(1))
                    if code:
                        index.exception_status[cls.name] = code
            classes_by_block[id(b)] = cls
            index.add_class(cls)

    # go: struct types declared as `type X struct {` are classes; also record
    # var -> type hints at file level for handler resolution
    if lang == "go":
        hints: Dict[str, str] = {}
        for m in _GO_VAR_TYPE_RX.finditer(sf.text):
            if m.group(1) and m.group(2):
                hints[m.group(1)] = m.group(2)
            elif m.group(3) and m.group(4):
                hints[m.group(3)] = m.group(4)[3:]   # NewHandler -> Handler
        index.module_vars[sf.path] = hints

    for b in blocks:
        if b.kind != "function":
            continue
        owner = b.receiver
        # nested function blocks inside a method are not members
        parent = b.parent
        while parent is not None and parent.kind != "class":
            if parent.kind == "function" and lang not in ("javascript", "typescript"):
                owner = ""
            parent = parent.parent
        fn = FunctionDef(name=b.name, file=sf, owner=owner, start=b.open + 1, end=b.close, line=b.line,
                         params=b.params, sig=b.sig, has_body=True, package=package if lang == "go" else "")
        index.add_function(fn)
        if owner:
            cls = index.class_named(owner, sf.path)
            if cls is not None:
                cls.methods.setdefault(fn.name, fn)
            elif lang == "go":
                # method on a type declared elsewhere (or a non-struct type): register a stub class
                stub = ClassDef(name=owner, file=sf, kind="struct")
                index.add_class(stub)
                stub.methods[fn.name] = fn

    # interface methods (no body) for Java/Kotlin/C#/TS so we can flag "interface only"
    if lang in ("java", "kotlin", "csharp", "typescript"):
        for b in blocks:
            if b.kind == "class" and b.class_kind == "interface":
                cls = classes_by_block.get(id(b))
                if cls is None:
                    continue
                body_masked = _blank_nested_blocks(masked, b.open + 1, b.close)
                base_off = b.open + 1
                for m in re.finditer(r"([A-Za-z_$][\w$]*)\s*\(", body_masked):
                    name = m.group(1)
                    if name in lexer.CONTROL_WORDS or name in cls.methods or name[0].isupper() and lang != "typescript":
                        continue
                    open_idx = m.end() - 1
                    close = lexer.match_bracket(body_masked, open_idx)
                    if close < 0:
                        continue
                    tail = body_masked[close + 1:close + 120]
                    if not re.match(r"\s*(?:throws\s+[\w.,\s]+)?(?::\s*[^;{]+)?\s*;", tail):
                        continue
                    # preceding text on the same declaration = return type + modifiers
                    decl_start = max(body_masked.rfind(";", 0, m.start()), body_masked.rfind("}", 0, m.start()), body_masked.rfind("{", 0, m.start())) + 1
                    sig = sf.text[base_off + decl_start:base_off + m.start()]
                    stub = FunctionDef(name=name, file=sf, owner=cls.name, has_body=False,
                                       params=sf.text[base_off + open_idx + 1:base_off + close], sig=sig,
                                       line=sf.lines.line(base_off + m.start()))
                    cls.methods[name] = stub
                    index.add_function(stub)

    # string constants: TOPIC = "order-events" style, used to label queue/cache/http steps
    consts: Dict[str, str] = {}
    for m in re.finditer(r"\b([A-Z][A-Z0-9_]{2,})\s*(?::\s*\w+\s*)?=\s*[\"'`]([^\"'`\n]{1,80})[\"'`]", sf.text):
        consts[m.group(1)] = m.group(2)
    for m in re.finditer(r"(?:const|val|final\s+String|static\s+final\s+String|private\s+static\s+final\s+String)\s+([A-Za-z_]\w*)\s*(?::\s*\w+\s*)?=\s*[\"'`]([^\"'`\n]{1,80})[\"'`]", sf.text):
        consts.setdefault(m.group(1), m.group(2))
    if consts:
        index.constants[sf.path] = consts

    # imports (JS/TS)
    if lang in ("javascript", "typescript"):
        imap: Dict[str, Tuple[Optional[str], str]] = {}
        for m in _JS_REQUIRE_RX.finditer(sf.text):
            target = index.repo.resolve_module(sf.path, m.group(2))
            tpath = target.path if target else None
            names = m.group(1)
            if names.startswith("{"):
                for part in names.strip("{} ").split(","):
                    part = part.strip()
                    if not part:
                        continue
                    if ":" in part:
                        exp, loc = [x.strip() for x in part.split(":", 1)]
                    else:
                        exp = loc = part
                    imap[loc] = (tpath, exp)
            else:
                imap[names] = (tpath, m.group(3) or "*")
        for m in _JS_IMPORT_RX.finditer(sf.text):
            target = index.repo.resolve_module(sf.path, m.group(2))
            tpath = target.path if target else None
            clause = m.group(1).strip()
            # default import, namespace import, named imports
            for piece in lexer.split_top_level(clause, ","):
                piece = piece.strip()
                if not piece:
                    continue
                if piece.startswith("{"):
                    for part in piece.strip("{} ").split(","):
                        part = part.strip()
                        if not part:
                            continue
                        if " as " in part:
                            exp, loc = [x.strip() for x in part.split(" as ", 1)]
                        else:
                            exp = loc = part
                        imap[loc] = (tpath, exp)
                elif piece.startswith("*"):
                    loc = piece.split(" as ", 1)[-1].strip()
                    imap[loc] = (tpath, "*")
                else:
                    imap[piece] = (tpath, "default")
        index.imports[sf.path] = imap
        # var -> ctor hints at module level (const svc = new OrderService())
        hints = {}
        for m in re.finditer(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*new\s+([A-Za-z_$][\w$]*)", sf.text):
            hints[m.group(1)] = m.group(2)
        index.module_vars[sf.path] = hints
    return blocks


def parse_params(lang: str, params: str) -> List[Tuple[str, str, str]]:
    """Return [(annotations_text, type, name)] for a brace-language parameter list."""
    out: List[Tuple[str, str, str]] = []
    if not params.strip():
        return out
    for raw in lexer.split_top_level(params, ","):
        raw = raw.strip()
        if not raw:
            continue
        ann = " ".join(re.findall(r"@[\w.]+(?:\([^)]*\))?|\[[^\]]*\]", raw))
        rest = re.sub(r"@[\w.]+(?:\([^)]*\))?|\[[^\]]*\]", " ", raw).strip()
        if lang in ("kotlin", "typescript", "javascript"):
            m = re.match(r"(?:(?:private|public|protected|readonly|val|var)\s+)*([A-Za-z_$][\w$]*)\s*\??\s*(?::\s*(.+))?$", rest, re.S)
            if m:
                out.append((ann, (m.group(2) or "").strip(), m.group(1)))
                continue
        else:
            m = re.match(r"(?:final\s+|params\s+|this\s+|ref\s+|out\s+|in\s+)?(.+?)\s+([A-Za-z_$@][\w$]*)\s*(?:=.*)?$", rest, re.S)
            if m:
                out.append((ann, m.group(1).strip(), m.group(2)))
                continue
        out.append((ann, "", rest))
    return out
