"""Redact secrets from code snippets before they land in a report.

Reports travel further than code (PR comments, Confluence, email), so any
literal that looks like a credential is masked. Patterns err on the side of
masking; a false positive only hides a value in a snippet."""
from __future__ import annotations

import re

_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|apikey|secret|password|passwd|pwd|token|access[_-]?key|private[_-]?key|client[_-]?secret|auth(?:orization)?)\b(\s*[:=]\s*|\s*,\s*)([\"'`])([^\"'`]{4,})([\"'`])"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)(https?://)([^/\s:@]+):([^/\s:@]+)@"),
]


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    out = _PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}«redacted»{m.group(5)}", out)
    for rx in _PATTERNS[1:8]:
        out = rx.sub("«redacted»", out)
    out = _PATTERNS[8].sub(lambda m: f"{m.group(1)}«redacted»:«redacted»@", out)
    return out
