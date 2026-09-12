"""Parse `@Annotation(args)` / `[Attribute(args)]` / `@decorator(args)` text."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from .. import lexer

_ANN_START = re.compile(r"@([A-Za-z_][\w.]*)")
_ATTR_START = re.compile(r"\[\s*([A-Za-z_][\w.]*)")


def find_annotations(sig: str, style: str = "at") -> List[Tuple[str, str]]:
    """Return [(name, raw_args)] for every annotation in a signature region.
    style 'at' handles @Name(...) (Java/Kotlin/TS decorators); 'bracket'
    handles C# [Name(...), Other] attributes."""
    out: List[Tuple[str, str]] = []
    masked = lexer.mask(sig, "java")
    if style == "bracket":
        i = 0
        while True:
            m = _ATTR_START.search(masked, i)
            if not m:
                break
            close = lexer.match_bracket(masked, m.start())
            if close < 0:
                break
            inner = sig[m.start() + 1:close]
            for part in lexer.split_top_level(inner, ","):
                pm = re.match(r"\s*([A-Za-z_][\w.]*)\s*(\((.*)\))?\s*$", part, re.S)
                if pm:
                    out.append((pm.group(1).split(".")[-1], pm.group(3) or ""))
            i = close + 1
        return out
    for m in _ANN_START.finditer(masked):
        name = m.group(1).split(".")[-1]
        j = m.end()
        while j < len(masked) and masked[j] in " \t":
            j += 1
        args = ""
        if j < len(masked) and masked[j] == "(":
            close = lexer.match_bracket(masked, j)
            if close > 0:
                args = sig[j + 1:close]
        out.append((name, args))
    return out


def parse_args(args: str) -> Dict[str, Any]:
    """`"/x"` -> {"value": "/x"}; `path = "/x", method = RequestMethod.GET` ->
    {"path": "/x", "method": "GET"}; `{"/a","/b"}` -> {"value": ["/a","/b"]}."""
    out: Dict[str, Any] = {}
    if not args or not args.strip():
        return out
    for part in lexer.split_top_level(args, ","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([A-Za-z_][\w]*)\s*[=:]\s*(.+)$", part, re.S)
        if m and not part.startswith(("'", '"', "{", "[")):
            key, val = m.group(1), m.group(2).strip()
        else:
            key, val = "value", part
        out[key] = _value(val)
    return out


def _value(val: str) -> Any:
    val = val.strip()
    if val.startswith("{") and val.endswith("}") or val.startswith("[") and val.endswith("]"):
        return [_value(v) for v in lexer.split_top_level(val[1:-1], ",")]
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'`":
        return val[1:-1]
    if val in ("true", "True"):
        return True
    if val in ("false", "False"):
        return False
    if re.match(r"^-?\d+$", val):
        return int(val)
    # RequestMethod.GET -> GET ; HttpStatus.CREATED -> CREATED ; MediaType.APPLICATION_JSON_VALUE -> APPLICATION_JSON_VALUE
    if re.match(r"^[\w.]+$", val) and "." in val:
        return val.split(".")[-1]
    return val


def as_list(v: Any) -> List[Any]:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]
