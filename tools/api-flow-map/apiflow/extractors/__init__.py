"""Extractor orchestration: index every file, then let each framework
extractor claim its endpoints."""
from __future__ import annotations

from typing import List

from ..discovery import Repo
from ..model import Endpoint
from .base import CodeIndex, EndpointSeed, index_brace_file
from . import dotnet, go_http, java_spring, js_ts, openapi, python_web, serverless


def build_index(repo: Repo) -> CodeIndex:
    index = CodeIndex(repo)
    for sf in repo.files:
        try:
            if sf.lang in ("java", "kotlin", "csharp", "javascript", "typescript", "go"):
                index.blocks[sf.path] = index_brace_file(sf, index)
            elif sf.lang == "python":
                sf._py_tree = python_web.index_python_file(sf, index)
        except Exception as exc:  # one odd file must not stop the scan
            repo.diagnostics.append(f"{sf.path}: indexing failed ({type(exc).__name__}: {exc})")
    try:
        java_spring.index_java_extras(index, repo.files_for("java", "kotlin"))
    except Exception as exc:
        repo.diagnostics.append(f"feign/repository indexing failed ({exc})")
    return index


def extract_all(repo: Repo, index: CodeIndex) -> List[EndpointSeed]:
    seeds: List[EndpointSeed] = []
    runners = [
        ("spring", lambda: java_spring.extract(index, repo.files_for("java", "kotlin"))),
        ("js/ts", lambda: js_ts.extract(index, repo.files_for("javascript", "typescript"))),
        ("python", lambda: python_web.extract(index, repo.files_for("python"))),
        ("go", lambda: go_http.extract(index, repo.files_for("go"))),
        ("dotnet", lambda: dotnet.extract(index, repo.files_for("csharp"))),
        ("serverless", lambda: serverless.extract(index, [f for f in repo.files_for("yaml") if f.name.lower().startswith(("serverless", "template", "sam"))], repo.diagnostics)),
    ]
    for name, fn in runners:
        try:
            seeds.extend(fn())
        except Exception as exc:
            repo.diagnostics.append(f"{name} extractor failed ({type(exc).__name__}: {exc})")
    return seeds


def extract_specs(repo: Repo) -> List[Endpoint]:
    files = [f for f in repo.files if f.lang in ("yaml", "json") and not f.name.lower().startswith(("serverless", "template", "sam"))]
    try:
        return openapi.extract(files, repo.diagnostics)
    except Exception as exc:
        repo.diagnostics.append(f"openapi extractor failed ({type(exc).__name__}: {exc})")
        return []
