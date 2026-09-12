"""Lightweight, resilient parsing for brace languages (Java, Kotlin, C#, JS/TS, Go).

We do not need a full grammar to explain a request flow. We need three things
that a regex alone cannot give us reliably:

1. `mask()`        — the source with strings and comments blanked (same length),
                     so braces/parens/keywords inside literals never fool us.
2. `scan_blocks()` — every `{ ... }` block with the "signature" text that
                     precedes it, classified as class / function / other.
3. `parse_block()` — a statement tree (if/else, switch, try, loops, return,
                     throw, expression statements, and lambda bodies inside
                     expressions) for a function body.

Everything degrades gracefully: unknown constructs become expression
statements, and unbalanced input simply stops the walk at file end.
Python is parsed with the real `ast` module in python_web.py instead.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

NEWLINE_TERMINATED = {"go", "javascript", "typescript", "kotlin"}
OPEN = "([{"
CLOSE = ")]}"
PAIR = {"(": ")", "[": "]", "{": "}"}

CONTROL_WORDS = {
    "if", "else", "for", "while", "do", "switch", "try", "catch", "finally", "return",
    "throw", "case", "default", "break", "continue", "when", "select", "foreach", "using",
    "lock", "synchronized", "static", "new", "yield", "with", "get", "set", "init",
    "unchecked", "checked", "fixed",
}


# --------------------------------------------------------------------------- masking

_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = {"return", "typeof", "instanceof", "in", "of", "case", "delete", "void", "throw", "do", "else"}


def mask(src: str, lang: str = "java") -> str:
    """Return a copy of `src` where comment text and string/char interiors are
    replaced with spaces (newlines preserved). Quote characters stay so that
    string boundaries remain visible."""
    n = len(src)
    out = list(src)
    i = 0
    js = lang in ("javascript", "typescript")
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        # comments
        if lang == "python":
            if c == "#":
                j = src.find("\n", i)
                if j < 0:
                    j = n
                for k in range(i, j):
                    out[k] = " "
                i = j
                continue
        elif c == "/" and nxt == "/":
            j = src.find("\n", i)
            if j < 0:
                j = n
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if c == "/" and nxt == "*" and lang != "python":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        # text blocks / raw strings
        if src.startswith('"""', i):
            j = src.find('"""', i + 3)
            j = n if j < 0 else j + 3
            for k in range(i + 3, j - 3):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        # C# verbatim string @"..." ("" is an escaped quote)
        if c == "@" and nxt == '"' and lang == "csharp":
            j = i + 2
            while j < n:
                if src[j] == '"':
                    if j + 1 < n and src[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            for k in range(i + 2, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
            continue
        if c in ('"', "'", "`"):
            quote = c
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    break
                if src[j] == "\n" and quote != "`" and not (lang == "go" and quote == "`"):
                    # unterminated single-line literal: stop at newline to stay resilient
                    break
                j += 1
            for k in range(i + 1, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
            continue
        # JS regex literal heuristic
        if js and c == "/" and nxt not in ("/", "*"):
            k = i - 1
            while k >= 0 and src[k] in " \t":
                k -= 1
            prev = src[k] if k >= 0 else "\n"
            word_end = k
            while k >= 0 and (src[k].isalnum() or src[k] == "_"):
                k -= 1
            prev_word = src[k + 1:word_end + 1]
            if prev in _REGEX_PRECEDERS or prev == "\n" or prev_word in _REGEX_KEYWORDS:
                j = i + 1
                in_class = False
                while j < n and src[j] != "\n":
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == "[":
                        in_class = True
                    elif src[j] == "]":
                        in_class = False
                    elif src[j] == "/" and not in_class:
                        break
                    j += 1
                for k2 in range(i + 1, min(j, n)):
                    out[k2] = " "
                i = j + 1
                continue
        i += 1
    return "".join(out)


# --------------------------------------------------------------------------- helpers

class LineIndex:
    def __init__(self, src: str):
        self.starts = [0]
        for m in re.finditer("\n", src):
            self.starts.append(m.end())

    def line(self, pos: int) -> int:
        return bisect.bisect_right(self.starts, pos)


def match_bracket(masked: str, i: int) -> int:
    """Index of the bracket that closes the one at `i`, or -1."""
    opener = masked[i]
    closer = PAIR.get(opener)
    if not closer:
        return -1
    depth = 0
    n = len(masked)
    j = i
    while j < n:
        c = masked[j]
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def skip_ws(text: str, i: int, end: int) -> int:
    while i < end and text[i].isspace():
        i += 1
    return i


def read_word(text: str, i: int) -> str:
    m = re.match(r"[A-Za-z_$][\w$]*", text[i:i + 64])
    return m.group(0) if m else ""


def split_top_level(text: str, sep: str = ",") -> List[str]:
    """Split on `sep` ignoring separators nested in brackets or quotes."""
    parts, depth, cur, quote = [], 0, [], ""
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            cur.append(c)
            if c == "\\":
                if i + 1 < len(text):
                    cur.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "\"'`":
            quote = c
            cur.append(c)
        elif c in OPEN:
            depth += 1
            cur.append(c)
        elif c in CLOSE:
            depth -= 1
            cur.append(c)
        elif c == sep and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def strip_string_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'`":
        return s[1:-1]
    return s


# --------------------------------------------------------------------------- block scanning

@dataclass
class Block:
    open: int                 # index of '{'
    close: int                # index of matching '}'
    sig_start: int            # start of the signature text region
    sig: str                  # raw signature text (original source, annotations included)
    depth: int                # brace nesting depth of the block
    parent: Optional["Block"] = None
    kind: str = "other"       # class | function | other
    name: str = ""
    params: str = ""
    receiver: str = ""        # Go receiver type / owning class name for methods
    class_kind: str = ""      # class | interface | enum | record | object | struct | trait
    extends: List[str] = field(default_factory=list)
    line: int = 0

    def annotations(self) -> str:
        return self.sig


_CLASS_RX = re.compile(
    r"\b(class|interface|enum|record|object|struct|trait)\s+([A-Za-z_$][\w$]*)"
)
_ANN_RX = re.compile(r"@[\w.]+")  # java/kotlin/ts annotations & decorators


def _strip_annotations(sig: str, lang: str) -> str:
    """Remove leading annotations / attributes / decorators from a signature."""
    s = sig
    while True:
        s2 = s.lstrip()
        if s2.startswith("@"):
            m = _ANN_RX.match(s2)
            if not m:
                break
            rest = s2[m.end():].lstrip()
            if rest.startswith("("):
                j = match_bracket(mask(rest, lang), 0)
                rest = rest[j + 1:] if j >= 0 else ""
            s = rest
            continue
        if lang == "csharp" and s2.startswith("["):
            j = match_bracket(mask(s2, lang), 0)
            s = s2[j + 1:] if j >= 0 else ""
            continue
        break
    return s.strip()


_MODIFIERS = (
    "public", "private", "protected", "internal", "static", "final", "abstract", "synchronized",
    "native", "default", "override", "async", "open", "suspend", "virtual", "sealed", "partial",
    "unsafe", "extern", "inline", "operator", "infix", "tailrec", "external", "const", "readonly",
    "export", "declare", "transient", "volatile", "strictfp", "new", "lateinit", "data", "inner",
    "companion", "annotation", "value", "actual", "expect", "unsafe", "fixed", "ref", "out",
)
_MOD_RX = re.compile(r"^(?:(?:%s)\s+)+" % "|".join(_MODIFIERS))

# language-specific function signature patterns (applied to a whitespace-collapsed
# signature with annotations removed)
_JAVA_FN = re.compile(r"(?:^|\s)([A-Za-z_$][\w$]*)\s*\((.*)\)\s*(?:throws\s+[\w.,\s<>]+)?\s*$", re.S)
_KOTLIN_FN = re.compile(r"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.<>?]+\.)?([A-Za-z_][\w]*)\s*\((.*)\)\s*(?::\s*[^{=]+)?$", re.S)
_CSHARP_FN = re.compile(r"(?:^|\s)([A-Za-z_][\w]*)\s*\((.*)\)\s*(?:where\s+[^{]+)?$", re.S)
_GO_FN = re.compile(r"\bfunc\s+(?:\(\s*(\w+)?\s*\*?\s*([\w.]+)\s*\)\s*)?([A-Za-z_]\w*)\s*\(", re.S)
_JS_FUNCTION = re.compile(r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)?\s*\((.*)\)\s*(?::\s*[^{]+)?$", re.S)
_JS_ARROW = re.compile(
    r"(?:(?:const|let|var)\s+|(?:[\w$.]+\.)?)?([A-Za-z_$][\w$]*)\s*(?:[:=]|=\s*)\s*(?:async\s*)?"
    r"(?:\((.*)\)|([A-Za-z_$][\w$]*))\s*(?::\s*[^=]+)?=>$", re.S)
_JS_METHOD = re.compile(
    r"(?:^|\s)(?:(?:static|async|get|set|public|private|protected|readonly|override|abstract)\s+)*"
    r"\*?\s*([A-Za-z_$#][\w$]*)\s*(?:<[^>]*>)?\s*\((.*)\)\s*(?::\s*[^{=]+)?$", re.S)
_CONSTRUCTOR_ONLY = re.compile(r"^[A-Z][\w$]*\s*\(")


def _classify_signature(sig_raw: str, lang: str) -> Tuple[str, str, str, str, List[str], str]:
    """Returns (kind, name, params, receiver, extends, class_kind)."""
    sig = _strip_annotations(sig_raw, lang)
    sig_ws = re.sub(r"\s+", " ", sig).strip()
    if not sig_ws:
        return "other", "", "", "", [], ""

    # class-like?
    m = _CLASS_RX.search(sig_ws)
    if m and not re.match(r"^(?:new)\b", sig_ws):
        head = sig_ws[:m.start()]
        # `x.class` or `.class.` would not be followed by an identifier; ok.
        if not re.search(r"[=(,]\s*$", head):
            ext = re.findall(r"(?:extends|implements|:)\s+([\w.<>, ]+)", sig_ws[m.end():])
            names: List[str] = []
            for grp in ext:
                for nm in grp.split(","):
                    nm = re.sub(r"<.*", "", nm).strip().split(".")[-1]
                    if nm and nm[0].isalpha():
                        names.append(nm)
            return "class", m.group(2), "", "", names, m.group(1)

    first = read_word(sig_ws, 0)
    if first in ("if", "else", "for", "while", "do", "switch", "try", "catch", "finally", "synchronized",
                 "return", "static", "using", "lock", "foreach", "when", "select", "with", "init"):
        return "other", "", "", "", [], ""
    if sig_ws.endswith("=>") and lang in ("csharp",):
        return "other", "", "", "", [], ""
    if re.search(r"(->|=>)\s*$", sig_ws) and lang not in ("javascript", "typescript"):
        return "other", "", "", "", [], ""

    if lang == "go":
        tm = re.search(r"\btype\s+([A-Za-z_]\w*)\s+(struct|interface)\s*$", sig_ws)
        if tm:
            return "class", tm.group(1), "", "", [], tm.group(2)
        m = _GO_FN.search(sig_ws)
        if m:
            params = ""
            close = match_bracket(sig_ws, m.end() - 1)
            if close > 0:
                params = sig_ws[m.end():close]
            recv_type = (m.group(2) or "").split(".")[-1]
            if m.group(1) and recv_type:
                params = f"{m.group(1)} *{recv_type}" + (", " + params if params.strip() else "")
            return "function", m.group(3), params, recv_type, [], ""
        return "other", "", "", "", [], ""

    if lang == "kotlin":
        m = _KOTLIN_FN.search(sig_ws)
        if m:
            return "function", m.group(1), m.group(2), "", [], ""
        # init blocks / property accessors
        return "other", "", "", "", [], ""

    if lang in ("javascript", "typescript"):
        if re.search(r"(->|=>)\s*$", sig_ws):
            m = _JS_ARROW.search(sig_ws)
            if m:
                return "function", m.group(1), m.group(2) or m.group(3) or "", "", [], ""
            return "other", "", "", "", [], ""   # anonymous arrow: callback body
        m = _JS_FUNCTION.search(sig_ws)
        if m:
            name = m.group(1) or ""
            if not name:
                # `const x = function (...)` / `exports.x = function (...)`
                m2 = re.search(r"([A-Za-z_$][\w$]*)\s*[:=]\s*(?:async\s*)?function\b", sig_ws)
                name = m2.group(1) if m2 else ""
            return ("function" if name else "other"), name, m.group(2), "", [], ""
        m = _JS_METHOD.search(sig_ws)
        if m and m.group(1) not in CONTROL_WORDS:
            name = m.group(1)
            before = sig_ws[:m.start(1)].rstrip()
            if before.endswith((".", "=", "return", "new", ",", "(")):
                return "other", "", "", "", [], ""
            return "function", name, m.group(2), "", [], ""
        return "other", "", "", "", [], ""

    # java / csharp (and anything else brace-y)
    rx = _CSHARP_FN if lang == "csharp" else _JAVA_FN
    sig_nomod = _MOD_RX.sub("", sig_ws)
    m = rx.search(" " + sig_nomod)
    if m:
        name = m.group(1)
        if name in CONTROL_WORDS:
            return "other", "", "", "", [], ""
        before = (" " + sig_nomod)[:m.start(1)].rstrip()
        if before.endswith(".") or before.endswith("=") or before.endswith("return") or before.endswith("new"):
            return "other", "", "", "", [], ""
        if lang == "csharp" and re.search(r"\b(get|set|add|remove)\s*$", before):
            return "other", "", "", "", [], ""
        return "function", name, m.group(2), "", [], ""
    return "other", "", "", "", [], ""


def scan_blocks(masked: str, src: str, lang: str, lines: Optional[LineIndex] = None) -> List[Block]:
    """Find every brace block that is not inside parentheses/brackets and
    classify it. Blocks are returned in source order with parent links so
    callers can find the owning class of a method."""
    lines = lines or LineIndex(src)
    blocks: List[Block] = []
    stack: List[Tuple[str, int, Optional[Block]]] = []   # (bracket, index, block-if-brace)
    open_blocks: List[Block] = []
    last_boundary = 0
    n = len(masked)
    i = 0
    while i < n:
        c = masked[i]
        if c in "([":
            stack.append((c, i, None))
        elif c == "{":
            inside_paren = bool(stack) and stack[-1][0] in "(["
            if inside_paren:
                stack.append((c, i, None))
            else:
                j = match_bracket(masked, i)
                sig = src[last_boundary:i]
                blk = Block(open=i, close=j if j >= 0 else n - 1, sig_start=last_boundary, sig=sig,
                            depth=len(open_blocks), parent=open_blocks[-1] if open_blocks else None,
                            line=lines.line(i))
                kind, name, params, receiver, ext, ckind = _classify_signature(sig, lang)
                blk.kind, blk.name, blk.params, blk.receiver, blk.extends, blk.class_kind = (
                    kind, name, params, receiver, ext, ckind)
                if kind == "function" and not receiver:
                    owner = blk.parent
                    while owner is not None and owner.kind != "class":
                        owner = owner.parent
                    blk.receiver = owner.name if owner else ""
                blocks.append(blk)
                stack.append((c, i, blk))
                open_blocks.append(blk)
                last_boundary = i + 1
        elif c in ")]}":
            if stack:
                opener, oi, blk = stack.pop()
                if c == "}" and blk is not None:
                    if open_blocks and open_blocks[-1] is blk:
                        open_blocks.pop()
                    last_boundary = i + 1
                elif c == "}" and opener == "{":
                    pass
            if c == "}" and (not stack or stack[-1][0] != "("):
                last_boundary = i + 1
        elif c == ";":
            if not stack or stack[-1][0] not in "([":
                last_boundary = i + 1
        i += 1
    return blocks


# --------------------------------------------------------------------------- statement tree

@dataclass
class Arm:
    label: str                 # if | else if | else | case X | default | try | catch X | finally
    cond: str
    body: List["Stmt"]


@dataclass
class Stmt:
    kind: str                  # expr | if | switch | try | loop | return | throw
    text: str                  # statement text (expr/return/throw) or subject
    line: int
    cond: str = ""
    arms: List[Arm] = field(default_factory=list)
    body: List["Stmt"] = field(default_factory=list)
    lambdas: List[List["Stmt"]] = field(default_factory=list)   # callback bodies inside an expression
    pos: int = 0


_STMT_END_CHARS = set(")]}\"'`") | set("0123456789")


class BlockParser:
    def __init__(self, src: str, masked: str, lang: str, lines: Optional[LineIndex] = None):
        self.src = src
        self.m = masked
        self.lang = lang
        self.lines = lines or LineIndex(src)
        self.newline_term = lang in NEWLINE_TERMINATED

    # -- public
    def parse(self, start: int, end: int) -> List[Stmt]:
        out: List[Stmt] = []
        i = start
        guard = 0
        while i < end and guard < 100000:
            guard += 1
            st, ni = self.parse_one(i, end)
            if ni <= i:
                ni = i + 1
            i = ni
            if st is not None:
                out.append(st)
        return out

    # -- internals
    def _stmt_end(self, i: int, end: int) -> int:
        m = self.m
        depth = 0
        j = i
        while j < end:
            c = m[j]
            if c in OPEN:
                depth += 1
            elif c in CLOSE:
                if depth == 0:
                    return j
                depth -= 1
            elif c == ";" and depth == 0:
                return j
            elif c == "\n" and depth == 0 and self.newline_term:
                if self._ends_at_newline(i, j, end):
                    return j
            j += 1
        return end

    def _ends_at_newline(self, start: int, nl: int, end: int) -> bool:
        """Approximation of automatic-semicolon rules for Go / JS / Kotlin."""
        k = nl - 1
        while k >= start and self.m[k] in " \t\r":
            k -= 1
        if k < start:
            return False
        last = self.m[k]
        ok_last = last.isalnum() or last == "_" or last in _STMT_END_CHARS
        if last in "+-" and k > start and self.m[k - 1] == last:   # x++ / x--
            ok_last = True
        if not ok_last:
            return False
        j = skip_ws(self.m, nl + 1, end)
        if j >= end:
            return True
        nxt = self.m[j]
        if self.lang in ("javascript", "typescript"):
            return nxt not in ".?:+-*/%&|^=,<>([`"
        if self.lang == "kotlin":
            return nxt not in ".?:+-*/%&|^=,<>"
        if self.lang == "go":
            return nxt != "."
        return True

    def parse_one(self, i: int, end: int) -> Tuple[Optional[Stmt], int]:
        m = self.m
        i = skip_ws(m, i, end)
        if i >= end:
            return None, end
        c = m[i]
        if c == ";" or c == "}" or c == ")" or c == "]":
            return None, i + 1
        if c == "{":
            j = match_bracket(m, i)
            if j < 0 or j > end:
                return None, end
            inner = self.parse(i + 1, j)
            st = Stmt("block", "", self.lines.line(i), body=inner, pos=i)
            return st, j + 1
        if c == "@" or (c == "[" and self.lang == "csharp"):
            # annotation on a local declaration; skip it
            j = i + 1
            while j < end and (m[j].isalnum() or m[j] in "_.$"):
                j += 1
            j = skip_ws(m, j, end)
            if j < end and m[j] == "(":
                k = match_bracket(m, j)
                j = k + 1 if k >= 0 else end
            return self.parse_one(j, end)
        word = read_word(m, i)
        if word == "if":
            return self._parse_if(i, end)
        if word == "switch" or (word == "when" and self.lang == "kotlin") or (word == "select" and self.lang == "go"):
            return self._parse_switch(i, end, word)
        if word == "try":
            return self._parse_try(i, end)
        if word in ("for", "while", "foreach"):
            return self._parse_loop(i, end, word)
        if word == "do":
            return self._parse_do(i, end)
        if word == "return":
            return self._parse_simple(i, end, "return", len(word))
        if word == "throw" or (word == "panic" and self.lang == "go"):
            return self._parse_simple(i, end, "throw", len(word) if word == "throw" else 0)
        if word in ("break", "continue", "fallthrough", "goto"):
            j = self._stmt_end(i, end)
            return None, j + 1
        if word in ("else",):
            # dangling else (should have been consumed) - parse its body as a block
            j = skip_ws(m, i + 4, end)
            st, ni = self.parse_one(j, end)
            return st, ni
        if word in ("case", "default") and self.lang != "kotlin":
            # stray case label outside of switch parsing: skip label
            j = m.find(":", i, end)
            return None, (j + 1 if j >= 0 else end)
        if word in ("defer", "go") and self.lang == "go":
            i2 = skip_ws(m, i + len(word), end)
            st, ni = self.parse_one(i2, end)
            return st, ni
        return self._parse_expr(i, end)

    def _read_paren_cond(self, i: int, end: int) -> Tuple[str, int]:
        """Read `( ... )` starting at i (after ws). Returns (cond_text, index after ')')."""
        m = self.m
        i = skip_ws(m, i, end)
        if i < end and m[i] == "(":
            j = match_bracket(m, i)
            if j < 0 or j > end:
                return self.src[i + 1:end].strip(), end
            return self.src[i + 1:j].strip(), j + 1
        # go-style: condition runs to the block opener
        j = i
        depth = 0
        while j < end:
            ch = m[j]
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == "{" and depth == 0:
                break
            elif ch == "\n" and depth == 0 and self.lang == "go":
                # go requires `{` on the same line; if we hit a newline first,
                # the condition continues (rare) — keep going
                pass
            j += 1
        return self.src[i:j].strip(), j

    def _parse_body(self, i: int, end: int) -> Tuple[List[Stmt], int]:
        m = self.m
        i = skip_ws(m, i, end)
        if i < end and m[i] == "{":
            j = match_bracket(m, i)
            if j < 0 or j > end:
                return self.parse(i + 1, end), end
            return self.parse(i + 1, j), j + 1
        st, ni = self.parse_one(i, end)
        return ([st] if st else []), ni

    def _parse_if(self, i: int, end: int) -> Tuple[Stmt, int]:
        m = self.m
        line = self.lines.line(i)
        arms: List[Arm] = []
        pos = i
        cond, j = self._read_paren_cond(i + 2, end)
        body, j = self._parse_body(j, end)
        arms.append(Arm("if", cond, body))
        while True:
            k = skip_ws(m, j, end)
            if read_word(m, k) != "else":
                break
            k2 = skip_ws(m, k + 4, end)
            if read_word(m, k2) == "if":
                cond, j = self._read_paren_cond(k2 + 2, end)
                body, j = self._parse_body(j, end)
                arms.append(Arm("else if", cond, body))
            else:
                body, j = self._parse_body(k2, end)
                arms.append(Arm("else", "", body))
                break
        return Stmt("if", "", line, cond=arms[0].cond, arms=arms, pos=pos), j

    def _parse_switch(self, i: int, end: int, word: str) -> Tuple[Stmt, int]:
        m = self.m
        line = self.lines.line(i)
        subject, j = self._read_paren_cond(i + len(word), end)
        j = skip_ws(m, j, end)
        if j >= end or m[j] != "{":
            return Stmt("switch", subject, line, pos=i), j
        close = match_bracket(m, j)
        if close < 0 or close > end:
            close = end
        arms = self._split_cases(j + 1, close)
        return Stmt("switch", subject, line, cond=subject, arms=arms, pos=i), close + 1

    def _split_cases(self, start: int, end: int) -> List[Arm]:
        """Split a switch/when body into arms. Supports `case X:`, `default:`,
        Java 14 `case X ->`, Go `case x:` and Kotlin `X ->` / `else ->`."""
        m = self.m
        labels: List[Tuple[int, int, str]] = []   # (label_start, body_start, label_text)
        if self.lang == "kotlin":
            arrows: List[int] = []
            depth = 0
            i = start
            while i < end:
                c = m[i]
                if c in OPEN:
                    depth += 1
                elif c in CLOSE:
                    depth -= 1
                elif depth == 0 and m.startswith("->", i):
                    arrows.append(i)
                    i += 2
                    continue
                i += 1
            for k in arrows:
                ls = m.rfind("\n", start, k)
                ls = start if ls < 0 else ls + 1
                labels.append((ls, k + 2, self.src[ls:k].strip()))
        else:
            i = start
            depth = 0
            while i < end:
                c = m[i]
                if c in OPEN:
                    depth += 1
                elif c in CLOSE:
                    depth -= 1
                elif depth == 0 and c.isalpha():
                    w = read_word(m, i)
                    prev = m[start:i].rstrip()[-1:] if m[start:i].strip() else ""
                    line_prefix = m[m.rfind("\n", start, i) + 1:i]
                    at_line_start = line_prefix.strip() == ""
                    if w in ("case", "default") and (at_line_start or prev in ("", "{", ";", "}", ":")):
                        k = i + len(w)
                        d2 = 0
                        while k < end:
                            ch = m[k]
                            if ch in OPEN:
                                d2 += 1
                            elif ch in CLOSE:
                                d2 -= 1
                            elif d2 == 0 and ch == ":" and m[k + 1:k + 2] != ":":
                                break
                            elif d2 == 0 and m.startswith("->", k):
                                k += 1
                                break
                            k += 1
                        labels.append((i, k + 1, self.src[i:k + 1].strip()))
                        i = k + 1
                        continue
                    i += max(1, len(w))
                    continue
                i += 1
        arms: List[Arm] = []
        for idx, (ls, bs, label) in enumerate(labels):
            be = labels[idx + 1][0] if idx + 1 < len(labels) else end
            body = self.parse(bs, be)
            low = label.lower()
            if low.startswith("default") or low == "else":
                arms.append(Arm("default", "", body))
            else:
                cond = re.sub(r"^case\s+", "", label).strip()
                cond = re.sub(r"\s*(:|->)$", "", cond).strip()
                arms.append(Arm("case " + cond, cond, body))
        return arms

    def _parse_try(self, i: int, end: int) -> Tuple[Stmt, int]:
        m = self.m
        line = self.lines.line(i)
        j = skip_ws(m, i + 3, end)
        resources = ""
        if j < end and m[j] == "(":
            k = match_bracket(m, j)
            resources = self.src[j + 1:k].strip() if k >= 0 else ""
            j = k + 1 if k >= 0 else end
        body, j = self._parse_body(j, end)
        arms = [Arm("try", resources, body)]
        while True:
            k = skip_ws(m, j, end)
            w = read_word(m, k)
            if w == "catch":
                k2 = skip_ws(m, k + 5, end)
                exc = ""
                if k2 < end and m[k2] == "(":
                    k3 = match_bracket(m, k2)
                    exc = self.src[k2 + 1:k3].strip() if k3 >= 0 else ""
                    k2 = k3 + 1 if k3 >= 0 else end
                    exc = re.sub(r"\s+\w+$", "", exc)  # drop variable name
                    exc = re.sub(r"^(?:final\s+)", "", exc)
                    exc = re.sub(r"\)\s*when\s*\(.*$", "", exc)
                body, j = self._parse_body(k2, end)
                arms.append(Arm("catch " + exc if exc else "catch", exc, body))
                continue
            if w == "finally":
                body, j = self._parse_body(k + 7, end)
                arms.append(Arm("finally", "", body))
                continue
            break
        return Stmt("try", "", line, arms=arms, pos=i), j

    def _parse_loop(self, i: int, end: int, word: str) -> Tuple[Stmt, int]:
        line = self.lines.line(i)
        cond, j = self._read_paren_cond(i + len(word), end)
        body, j = self._parse_body(j, end)
        return Stmt("loop", word, line, cond=cond, body=body, pos=i), j

    def _parse_do(self, i: int, end: int) -> Tuple[Stmt, int]:
        m = self.m
        line = self.lines.line(i)
        body, j = self._parse_body(i + 2, end)
        k = skip_ws(m, j, end)
        cond = ""
        if read_word(m, k) == "while":
            cond, k = self._read_paren_cond(k + 5, end)
            k = self._stmt_end(k, end) + 1
        return Stmt("loop", "do", line, cond=cond, body=body, pos=i), k

    def _parse_simple(self, i: int, end: int, kind: str, wordlen: int) -> Tuple[Stmt, int]:
        line = self.lines.line(i)
        j = self._stmt_end(i + wordlen, end)
        ts = skip_ws(self.m, i + wordlen, j)
        text = self.src[ts:j].rstrip()
        st = Stmt(kind, text, line, pos=ts)
        st.lambdas = self._lambda_bodies(i + wordlen, j)
        nxt = j + 1 if j < end and self.m[j] == ";" else j
        return st, nxt

    def _parse_expr(self, i: int, end: int) -> Tuple[Optional[Stmt], int]:
        line = self.lines.line(i)
        j = self._stmt_end(i, end)
        if j <= i:
            return None, i + 1
        text = self.src[i:j].rstrip()
        st = Stmt("expr", text, line, pos=i)
        st.lambdas = self._lambda_bodies(i, j)
        nxt = j + 1 if j < end and self.m[j] == ";" else j
        return st, nxt

    def _lambda_bodies(self, start: int, end: int) -> List[List[Stmt]]:
        """Parse `-> { ... }` / `=> { ... }` / `function (...) { ... }` bodies that
        live inside an expression (callbacks, promise chains, stream lambdas)."""
        m = self.m
        out: List[List[Stmt]] = []
        i = start
        while i < end:
            k = m.find("{", i, end)
            if k < 0:
                break
            before = m[max(start, k - 200):k].rstrip()
            is_fn_body = (before.endswith("->") or before.endswith("=>") or before.endswith(")")
                          or re.search(r"\bfunction\s*\*?\s*\w*\s*$", before) is not None)
            j = match_bracket(m, k)
            if j < 0 or j > end:
                break
            if is_fn_body and not re.search(r"(?:new\s+[\w.<>]+\s*\([^()]*\))\s*$", before):
                out.append(self.parse(k + 1, j))
            i = j + 1
        return out


def parse_body(src: str, masked: str, lang: str, start: int, end: int, lines: Optional[LineIndex] = None) -> List[Stmt]:
    return BlockParser(src, masked, lang, lines).parse(start, end)


def iter_stmts(stmts: List[Stmt]) -> Iterator[Stmt]:
    for s in stmts:
        yield s
        for a in s.arms:
            yield from iter_stmts(a.body)
        yield from iter_stmts(s.body)
        for lb in s.lambdas:
            yield from iter_stmts(lb)
