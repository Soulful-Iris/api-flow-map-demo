"""Repository discovery: which files to read, in which language, with which config.

Design notes
- Inside a git checkout we ask git for the file list (`git ls-files` incl.
  untracked-but-not-ignored) so .gitignore semantics are exact and local,
  uncommitted files are seen. Outside git (e.g. an extracted base snapshot)
  we walk the tree with a conservative default ignore list.
- Test code is excluded by default: test routers and mocks would otherwise
  show up as endpoints and pollute call resolution.
- Every file is read once, lazily masked, and capped in size so a vendored
  bundle cannot stall the scan.
"""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from . import lexer

EXT_LANG = {
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".cs": "csharp",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".py": "python",
    ".go": "go",
    ".yaml": "yaml", ".yml": "yaml", ".json": "json",
}

UNSUPPORTED_HINTS = {".rb": "Ruby", ".php": "PHP", ".scala": "Scala", ".rs": "Rust", ".ex": "Elixir", ".exs": "Elixir"}

DEFAULT_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "target", "out", "bin", "obj", "vendor",
    ".venv", "venv", "env", "__pycache__", ".idea", ".vscode", "coverage", ".next", ".nuxt", ".terraform",
    ".gradle", ".mvn", ".settings", "site-packages", "bower_components", ".cache", ".pytest_cache",
    ".mypy_cache", ".tox", "generated", "__generated__", ".serverless", ".aws-sam", "cdk.out", ".ruff_cache",
    "htmlcov", "logs", "tmp", ".apiflow",
}
DEFAULT_TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testing", "e2e", "integration-tests", "mocks", "__mocks__", "fixtures"}
DEFAULT_TEST_FILE_GLOBS = ["*_test.go", "*.test.js", "*.test.ts", "*.test.jsx", "*.test.tsx", "*.spec.js", "*.spec.ts",
                           "*Test.java", "*Tests.java", "*IT.java", "*Spec.kt", "*Test.kt", "test_*.py", "*_test.py",
                           "conftest.py", "*Tests.cs", "*Test.cs", "*.stories.tsx", "*.d.ts", "*.min.js"]

SPEC_FILE_NAMES = {"openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml", "swagger.yml",
                   "api.yaml", "api.yml", "api-spec.yaml", "api-spec.yml"}
SERVERLESS_FILE_NAMES = {"serverless.yml", "serverless.yaml", "template.yaml", "template.yml", "sam.yaml", "sam.yml"}


@dataclass
class Config:
    include: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    include_tests: bool = False
    max_file_kb: int = 768
    max_files: int = 20000
    max_depth: int = 4
    max_steps_per_endpoint: int = 400
    base_branch: str = ""
    io_patterns: Dict[str, List[str]] = field(default_factory=dict)
    noise_patterns: List[str] = field(default_factory=list)
    auth_patterns: List[str] = field(default_factory=list)
    validation_patterns: List[str] = field(default_factory=list)
    feature_flag_patterns: List[str] = field(default_factory=list)
    hide_kinds: List[str] = field(default_factory=lambda: ["log"])
    redact: bool = True
    snippets: bool = True
    endpoint_titles: Dict[str, str] = field(default_factory=dict)
    raw: Dict = field(default_factory=dict)

    @staticmethod
    def load(root: str, overrides: Optional[Dict] = None) -> "Config":
        cfg = Config()
        data: Dict = {}
        for name in (".apiflow.json", "apiflow.json", ".apiflow.yaml", ".apiflow.yml"):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                try:
                    if name.endswith(".json"):
                        with open(p, "r", encoding="utf-8") as fh:
                            data = json.load(fh) or {}
                    else:
                        data = _load_yaml(p) or {}
                except Exception as exc:  # config errors must not kill a scan
                    data = {"_error": f"could not parse {name}: {exc}"}
                break
        if overrides:
            data.update({k: v for k, v in overrides.items() if v is not None})
        cfg.raw = data
        for k, v in data.items():
            if hasattr(cfg, k) and k != "raw":
                setattr(cfg, k, v)
        return cfg


def _load_yaml(path: str):
    try:
        import yaml  # type: ignore
    except Exception:
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass
class SourceFile:
    path: str            # posix path relative to repo root
    abspath: str
    lang: str
    text: str
    _masked: Optional[str] = None
    _lines: Optional[lexer.LineIndex] = None

    @property
    def masked(self) -> str:
        if self._masked is None:
            self._masked = lexer.mask(self.text, self.lang)
        return self._masked

    @property
    def lines(self) -> lexer.LineIndex:
        if self._lines is None:
            self._lines = lexer.LineIndex(self.text)
        return self._lines

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def stem(self) -> str:
        return os.path.splitext(self.name)[0]


class Repo:
    def __init__(self, root: str, config: Optional[Config] = None):
        self.root = os.path.abspath(root)
        self.config = config or Config.load(self.root)
        self.files: List[SourceFile] = []
        self.by_path: Dict[str, SourceFile] = {}
        self.skipped: List[str] = []
        self.unsupported: Dict[str, int] = {}
        self.diagnostics: List[str] = []
        if "_error" in self.config.raw:
            self.diagnostics.append(self.config.raw["_error"])

    # ------------------------------------------------------------------ listing
    def _git_files(self) -> Optional[List[str]]:
        if not os.path.isdir(os.path.join(self.root, ".git")) and not os.path.isfile(os.path.join(self.root, ".git")):
            return None
        try:
            out = subprocess.run(
                ["git", "-C", self.root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                capture_output=True, check=True, timeout=120,
            ).stdout
        except Exception:
            return None
        return [p for p in out.decode("utf-8", "replace").split("\0") if p]

    def _walk_files(self) -> List[str]:
        out: List[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.startswith("."))
            rel_dir = os.path.relpath(dirpath, self.root)
            for fn in sorted(filenames):
                rel = fn if rel_dir == "." else os.path.join(rel_dir, fn)
                out.append(rel.replace(os.sep, "/"))
                if len(out) > self.config.max_files:
                    self.diagnostics.append(f"file limit reached ({self.config.max_files}); scan is partial")
                    return out
        return out

    def _is_test_path(self, rel: str) -> bool:
        parts = rel.split("/")
        if any(p in DEFAULT_TEST_DIRS for p in parts[:-1]):
            return True
        if "src/test" in rel or "/test/" in ("/" + rel):
            return True
        return any(fnmatch.fnmatch(parts[-1], g) for g in DEFAULT_TEST_FILE_GLOBS)

    def _excluded(self, rel: str) -> bool:
        parts = rel.split("/")
        if any(p in DEFAULT_IGNORE_DIRS for p in parts[:-1]):
            return True
        for g in self.config.exclude:
            if fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(parts[-1], g):
                return True
        if self.config.include:
            if not any(fnmatch.fnmatch(rel, g) for g in self.config.include):
                return True
        if not self.config.include_tests and self._is_test_path(rel):
            return True
        return False

    def load(self) -> "Repo":
        listed = self._git_files()
        if listed is None:
            listed = self._walk_files()
        limit = self.config.max_file_kb * 1024
        for rel in sorted(listed):
            if self._excluded(rel):
                continue
            ext = os.path.splitext(rel)[1].lower()
            lang = EXT_LANG.get(ext)
            base = os.path.basename(rel).lower()
            if lang in ("yaml", "json"):
                if base not in SPEC_FILE_NAMES and base not in SERVERLESS_FILE_NAMES and not base.endswith(("openapi.yaml", "openapi.yml", "openapi.json", "swagger.json", "swagger.yaml")):
                    continue
            if lang is None:
                if ext in UNSUPPORTED_HINTS:
                    self.unsupported[UNSUPPORTED_HINTS[ext]] = self.unsupported.get(UNSUPPORTED_HINTS[ext], 0) + 1
                continue
            absp = os.path.join(self.root, rel)
            try:
                size = os.path.getsize(absp)
            except OSError:
                continue
            if size > limit:
                self.skipped.append(f"{rel} ({size // 1024} KB > {self.config.max_file_kb} KB)")
                continue
            try:
                with open(absp, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            if "\0" in text[:4096]:
                continue
            sf = SourceFile(path=rel, abspath=absp, lang=lang, text=text)
            self.files.append(sf)
            self.by_path[rel] = sf
        if self.skipped:
            self.diagnostics.append(f"skipped {len(self.skipped)} oversized file(s): " + ", ".join(self.skipped[:5]) + (" ..." if len(self.skipped) > 5 else ""))
        for lang, n in sorted(self.unsupported.items()):
            self.diagnostics.append(f"{n} {lang} file(s) present; {lang} is not supported yet, so those endpoints are not mapped")
        return self

    def files_for(self, *langs: str) -> Iterable[SourceFile]:
        return [f for f in self.files if f.lang in langs]

    def resolve_module(self, from_file: str, spec: str) -> Optional[SourceFile]:
        """Resolve a relative JS/TS/Python import to a file in the repo."""
        if not spec.startswith("."):
            # bare specifier: try path aliases '@/x' or 'src/x'
            spec2 = spec.lstrip("@/")
            cands = [spec2, "src/" + spec2]
        else:
            base_dir = os.path.dirname(from_file)
            cands = [os.path.normpath(os.path.join(base_dir, spec)).replace(os.sep, "/")]
        for cand in cands:
            for suffix in ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", "/index.ts", "/index.js", "/__init__.py"):
                p = cand + suffix
                if p in self.by_path:
                    return self.by_path[p]
        return None
