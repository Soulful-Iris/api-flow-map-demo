"""Heuristic, deterministic titles for endpoints, steps and conditions.

These are the "auto" labels. They read well enough for a first pass and are
stable across runs (which the differ relies on). Claude improves them via
annotations when a human is going to read the report; see
references/writing-titles.md.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .httpcodes import label as status_label

_CAMEL_RX = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_ACRONYMS = {"dto", "dtos", "vo", "vos", "pk", "id", "ids", "url", "uri", "http", "https", "api", "json", "xml", "sql", "db", "uuid", "jwt", "sso", "otp", "kyc", "aml", "pdf", "csv", "html", "css", "ip", "tcp", "dns", "aws", "s3", "sqs", "sns", "iam", "ui", "ux", "html", "cpu", "ram", "io", "os", "vm", "ci", "cd", "acl", "rbac", "ach", "ssn", "pii", "pci"}
VERB_BY_METHOD = {"GET": "Get", "POST": "Create", "PUT": "Replace", "PATCH": "Update", "DELETE": "Delete", "HEAD": "Check", "OPTIONS": "Describe", "ANY": "Handle"}
GENERIC_HANDLER_NAMES = {"handle", "handler", "handleRequest", "index", "main", "run", "invoke", "execute", "process", "get", "post", "put", "patch", "delete", "call", "lambda_handler", "inline"}


def split_words(name: str) -> List[str]:
    name = name.strip("_$#")
    if not name:
        return []
    name = name.replace("-", "_")
    parts: List[str] = []
    for chunk in name.split("_"):
        if not chunk:
            continue
        parts.extend(p for p in _CAMEL_RX.split(chunk) if p)
    words = []
    for p in parts:
        low = p.lower()
        words.append(low.upper() if low in _ACRONYMS else low)
    return words


def humanize(name: str) -> str:
    """getOrderById -> 'get order by ID'; order_total -> 'order total'."""
    return " ".join(split_words(name))


def sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    return text[0].upper() + text[1:]


def singular(word: str) -> str:
    w = word
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("ses", "xes", "zes", "ches", "shes")) and len(w) > 4:
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def type_words(type_name: str) -> str:
    t = re.sub(r"<.*", "", type_name or "").split(".")[-1]
    t = re.sub(r"(Impl|Default|Abstract|Base)$", "", t)
    t = re.sub(r"^(Default|Abstract|Base|I(?=[A-Z]))", "", t)
    return humanize(t)


# ---------------------------------------------------------------------------- endpoints

def endpoint_title(method: str, path: str, handler_name: str, spec_summary: str = "", overrides: Optional[Dict[str, str]] = None) -> str:
    key = f"{method.upper()} {path}"
    if overrides and key in overrides:
        return overrides[key]
    if spec_summary:
        return sentence(spec_summary.strip().rstrip("."))
    segs = [s for s in path.split("/") if s]
    if segs and segs[-1].lower() in _OPS_TITLES and method.upper() in ("GET", "ANY", "HEAD"):
        return _OPS_TITLES[segs[-1].lower()]
    fn = handler_name.split(".")[-1] if handler_name else ""
    fn_words = split_words(fn) if fn and not fn.startswith("(") and " " not in fn else []
    if fn_words and fn not in GENERIC_HANDLER_NAMES and fn_words[0] not in ("get", "post", "put", "patch", "delete", "handle", "on"):
        return sentence(" ".join(fn_words))
    if fn_words and fn not in GENERIC_HANDLER_NAMES and len(fn_words) > 1:
        return sentence(" ".join(fn_words))
    return _title_from_route(method, path)


_OPS_TITLES = {"health": "Health check", "healthz": "Health check", "healthcheck": "Health check", "health-check": "Health check",
               "ping": "Ping", "ready": "Readiness check", "readyz": "Readiness check", "readiness": "Readiness check",
               "live": "Liveness check", "livez": "Liveness check", "liveness": "Liveness check", "status": "Service status",
               "metrics": "Metrics", "version": "Version info", "info": "Service info", "actuator": "Actuator"}


def _title_from_route(method: str, path: str) -> str:
    segs = [s for s in path.split("/") if s]
    verb = VERB_BY_METHOD.get(method.upper(), method.title())
    if not segs:
        return f"{verb} root"
    if segs[-1].lower() in _OPS_TITLES and method.upper() in ("GET", "ANY", "HEAD"):
        return _OPS_TITLES[segs[-1].lower()]
    last = segs[-1]
    is_param = last.startswith("{") or last.startswith(":")
    static = [s for s in segs if not (s.startswith("{") or s.startswith(":"))]
    static = [s for s in static if not re.match(r"^(api|v\d+|internal|public|private|rest)$", s, re.I)]
    resource = humanize(static[-1]) if static else "resource"
    if not is_param and len(segs) >= 2 and (segs[-2].startswith("{") or segs[-2].startswith(":")) and method.upper() in ("POST", "PUT", "PATCH"):
        # POST /orders/{id}/cancel -> "Cancel order"
        parent = humanize(static[-2]) if len(static) >= 2 else "resource"
        return sentence(f"{humanize(last)} {singular(parent)}")
    if is_param:
        pname = humanize(last.strip("{}:"))
        return sentence(f"{verb} {singular(resource)} by {pname}")
    if method.upper() == "GET":
        return sentence(f"List {resource}") if resource.endswith("s") else sentence(f"Get {resource}")
    if method.upper() == "POST":
        return sentence(f"Create {singular(resource)}")
    return sentence(f"{verb} {resource}")


# ---------------------------------------------------------------------------- conditions

_ARG = r"((?:[^()]|\([^()]*\))*)"


def _neg_word(m):
    return f"{m.group(1)} is not {humanize(m.group(2))}"


# Stage 1: language-specific negation forms (applied before anything else, so
# they never see text we generated ourselves).
_NEG_RULES = [
    (re.compile(r"\.get([A-Z]\w*)\(\)"), lambda m: "." + m.group(1)[0].lower() + m.group(1)[1:]),
    (re.compile(r"!\s*errors\.Is\(\s*err\s*,\s*([\w.]+)\s*\)"), r"the error is not \1"),
    (re.compile(r"!\s*Objects\.equals\(" + _ARG + r"\)"), lambda m: " is not ".join(x.strip() for x in m.group(1).split(",", 1))),
    (re.compile(r"!\s*StringUtils\.hasText\(" + _ARG + r"\)"), r"\1 is blank"),
    (re.compile(r"!\s*StringUtils\.isBlank\(" + _ARG + r"\)"), r"\1 is not blank"),
    (re.compile(r"!\s*StringUtils\.isEmpty\(" + _ARG + r"\)"), r"\1 is not empty"),
    (re.compile(r"!\s*Objects\.isNull\(" + _ARG + r"\)"), r"\1 is present"),
    (re.compile(r"!\s*Objects\.nonNull\(" + _ARG + r"\)"), r"\1 is missing"),
    (re.compile(r"!\s*CollectionUtils\.isEmpty\(" + _ARG + r"\)"), r"\1 has items"),
    (re.compile(r"!\s*string\.IsNullOrEmpty\(" + _ARG + r"\)"), r"\1 is present"),
    (re.compile(r"!\s*string\.IsNullOrWhiteSpace\(" + _ARG + r"\)"), r"\1 is not blank"),
    (re.compile(r"!\s*(?<![.\w])is([A-Z]\w*)\(" + _ARG + r"\)"), lambda m: f"{m.group(2)} is not {humanize(m.group(1))}"),
    (re.compile(r"!\s*(\w+(?:\.\w+)*)\s*\.isPresent\(\)"), r"\1 is missing"),
    (re.compile(r"!\s*(\w+(?:\.\w+)*)\s*\.isEmpty\(\)"), r"\1 has items"),
    (re.compile(r"!\s*(\w+(?:\.\w+)*)\s*\.isBlank\(\)"), r"\1 is not blank"),
    (re.compile(r"!\s*(\w+(?:\.\w+)*)\s*\.equals\(\s*([^)]*)\)"), r"\1 is not \2"),
    (re.compile(r"!\s*(\w+(?:\.\w+)*)\s*\.equalsIgnoreCase\(\s*([^)]*)\)"), r"\1 is not \2"),
    (re.compile(r"!\s*(\w+(?:\.\w+)*)\s*\.contains\(\s*([^)]*)\)"), r"\1 does not contain \2"),
    (re.compile(r"!\s*(\w+(?:\.\w+)*)\s*\.containsKey\(\s*([^)]*)\)"), r"\1 has no \2"),
    (re.compile(r"!\s*(\w+(?:\.\w+)*)\s*\.includes\(\s*([^)]*)\)"), r"\1 does not include \2"),
    (re.compile(r"!\s*(\w+(?:\.\w+)*)\s*\.(some|any)\(\s*([^)]*)\)"), r"none of \1 match"),
    (re.compile(r"!\s*(\w+(?:\.\w+)*)\s*\.is([A-Z]\w*)\(\)"), _neg_word),
    (re.compile(r"!\s*(\w+(?:\.\w+)*)\s*\.has([A-Z]\w*)\(\)"), lambda m: f"{m.group(1)} has no {humanize(m.group(2))}"),
    (re.compile(r"!\s*(\w+(?:\.\w+)*)\s*\.can([A-Z]\w*)\(\)"), lambda m: f"{m.group(1)} cannot {humanize(m.group(2))}"),
    (re.compile(r"!\s*(\w+(?:\.\w+)*)\s*\.(\w+)\(([^()]*)\)"), lambda m: f"not {m.group(1)} {humanize(m.group(2))}" + (f" {m.group(3)}" if m.group(3).strip() else "")),
    (re.compile(r"!\s*(\w+)\(([^()]*)\)"), lambda m: f"not {humanize(m.group(1))}" + (f" {m.group(2)}" if m.group(2).strip() else "")),
    (re.compile(r"(^|[\s(])!\s*(\w+(?:\.\w+)*)\b(?!\s*\()"), r"\1\2 is missing"),
    (re.compile(r"(^|[\s(])!\s*\("), r"\1not ("),
]
_PY_NEG_RULES = [
    (re.compile(r"\bnot\s+isinstance\(([^,]+),\s*([^)]+)\)"), r"\1 is not a \2"),
    (re.compile(r"\bnot\s+(\w+(?:\.\w+)*?)\.is_(\w+)\b(?!\s*\()"), lambda m: f"{m.group(1)} is not {humanize(m.group(2))}"),
    (re.compile(r"\bnot\s+(\w+(?:\.\w+)*?)\.is_(\w+)\(\)"), lambda m: f"{m.group(1)} is not {humanize(m.group(2))}"),
    (re.compile(r"(?<!is )\bnot\s+(\w+(?:\.\w+)*)\.(\w+)\(([^()]*)\)"), lambda m: f"not {m.group(1)} {humanize(m.group(2))}" + (f" {m.group(3)}" if m.group(3).strip() else "")),
    (re.compile(r"(?<!is )\bnot\s+(\w+(?:\.\w+)*)\b(?!\s*\()"), r"\1 is missing"),
]

# Stage 2: positive idioms, then operators.
_COND_REPLACEMENTS = [
    (re.compile(r"\.get([A-Z]\w*)\(\)"), lambda m: "." + m.group(1)[0].lower() + m.group(1)[1:]),
    (re.compile(r"(?:\w+\.)*(?:isEnabled|is_enabled|IsEnabled|Enabled|boolVariation|BoolVariation|getTreatment|GetTreatment|isOn|IsOn|isFeatureEnabled|variation|Variation|isActive|enabled)\(\s*['\"]([^'\"]+)['\"][^)]*\)"), r"feature flag '\1' is on"),
    (re.compile(r"\bStringUtils\.isBlank\(" + _ARG + r"\)"), r"\1 is blank"),
    (re.compile(r"\bStringUtils\.isNotBlank\(" + _ARG + r"\)"), r"\1 is not blank"),
    (re.compile(r"\bStringUtils\.isEmpty\(" + _ARG + r"\)"), r"\1 is empty"),
    (re.compile(r"\bStringUtils\.isNotEmpty\(" + _ARG + r"\)"), r"\1 is not empty"),
    (re.compile(r"\bObjects\.equals\(" + _ARG + r"\)"), lambda m: " is ".join(x.strip() for x in m.group(1).split(",", 1))),
    (re.compile(r"\bStringUtils\.hasText\(" + _ARG + r"\)"), r"\1 is not blank"),
    (re.compile(r"\bObjects\.isNull\(" + _ARG + r"\)"), r"\1 is missing"),
    (re.compile(r"\bObjects\.nonNull\(" + _ARG + r"\)"), r"\1 is present"),
    (re.compile(r"\bCollectionUtils\.isEmpty\(" + _ARG + r"\)"), r"\1 is empty"),
    (re.compile(r"(?<![.\w])is([A-Z]\w*)\(" + _ARG + r"\)"), lambda m: f"{m.group(2)} is {humanize(m.group(1))}"),
    (re.compile(r"\berr\s*!=\s*nil\b"), "an error occurred"),
    (re.compile(r"\berr\s*==\s*nil\b"), "no error occurred"),
    (re.compile(r"\bisNullOrEmpty\(([^)]*)\)"), r"\1 is missing or empty"),
    (re.compile(r"\bstring\.IsNullOrEmpty\(([^)]*)\)"), r"\1 is missing or empty"),
    (re.compile(r"\bstring\.IsNullOrWhiteSpace\(([^)]*)\)"), r"\1 is blank"),
    (re.compile(r"\berrors\.Is\(\s*err\s*,\s*([\w.]+)\s*\)"), r"the error is \1"),
    (re.compile(r"\berrors\.As\(\s*err\s*,\s*&?([\w.]+)\s*\)"), r"the error is a \1"),
    (re.compile(r"\b(?:\w+\.)?Err([A-Z]\w*)\b"), lambda m: humanize(m.group(1))),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.isPresent\(\)"), r"\1 is present"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.isEmpty\(\)"), r"\1 is empty"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.isBlank\(\)"), r"\1 is blank"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.size\(\)\s*(?:==\s*0)"), r"\1 is empty"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.size\(\)\s*>\s*0"), r"\1 has items"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.length\s*(?:===?\s*0)"), r"\1 is empty"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.length\s*>\s*0"), r"\1 has items"),
    (re.compile(r"\blen\(([^)]*)\)\s*==\s*0"), r"\1 is empty"),
    (re.compile(r"\blen\(([^)]*)\)\s*>\s*0"), r"\1 has items"),
    (re.compile(r"\.compareTo\(([^)]*)\)\s*>=\s*0"), r" is at least \1"),
    (re.compile(r"\.compareTo\(([^)]*)\)\s*>\s*0"), r" is greater than \1"),
    (re.compile(r"\.compareTo\(([^)]*)\)\s*<=\s*0"), r" is at most \1"),
    (re.compile(r"\.compareTo\(([^)]*)\)\s*<\s*0"), r" is less than \1"),
    (re.compile(r"\.compareTo\(([^)]*)\)\s*==\s*0"), r" equals \1"),
    (re.compile(r"\.compareTo\(([^)]*)\)\s*!=\s*0"), r" differs from \1"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.equals\(\s*([^)]*)\)"), r"\1 is \2"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.equalsIgnoreCase\(\s*([^)]*)\)"), r"\1 is \2"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.contains\(\s*([^)]*)\)"), r"\1 contains \2"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.containsKey\(\s*([^)]*)\)"), r"\1 has \2"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.startsWith\(\s*([^)]*)\)"), r"\1 starts with \2"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.includes\(\s*([^)]*)\)"), r"\1 includes \2"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.hasRole\(\s*([^)]*)\)"), r"\1 has role \2"),
    (re.compile(r"\binstanceof\b"), "is a"),
    (re.compile(r"\bisinstance\(([^,]+),\s*([^)]+)\)"), r"\1 is a \2"),
    (re.compile(r"\bhasattr\(([^,]+),\s*([^)]+)\)"), r"\1 has \2"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*(?:==|===)\s*(?:null|nil|undefined|None)\b"), r"\1 is missing"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*(?:!=|!==)\s*(?:null|nil|undefined|None)\b"), r"\1 is present"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s+is\s+None\b"), r"\1 is missing"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s+is\s+not\s+None\b"), r"\1 is present"),
    (re.compile(r"\berr\s*!=\s*nil\b"), "an error occurred"),
    (re.compile(r"\berr\s*==\s*nil\b"), "no error occurred"),
    (re.compile(r"\b(\w+(?:\.\w+)*)\s*\.is_(\w+)\b"), lambda m: f"{m.group(1)} is {humanize(m.group(2))}"),
    (re.compile(r"\.is([A-Z]\w*)\(\)"), lambda m: " is " + humanize(m.group(1))),
    (re.compile(r"\.has([A-Z]\w*)\(\)"), lambda m: " has " + humanize(m.group(1))),
    (re.compile(r"\s*&&\s*|\s+and\s+"), " and "),
    (re.compile(r"\s*\|\|\s*|\s+or\s+"), " or "),
    (re.compile(r"\s*!==?\s*"), " is not "),
    (re.compile(r"\s*===?\s*"), " is "),
    (re.compile(r"\s*>=\s*"), " ≥ "),
    (re.compile(r"\s*<=\s*"), " ≤ "),
    (re.compile(r"\s*>\s*"), " > "),
    (re.compile(r"\s*<\s*"), " < "),
]
_FLAG_RX = re.compile(r"(featureFlag|FeatureFlag|feature_flag|isEnabled|is_enabled|isFeatureEnabled|boolVariation|toggle|Toggle|unleash|flagsmith|getTreatment|isOn\(|flags?\.|Flag\.|ldClient|launchdarkly|LaunchDarkly)", re.I)


def is_feature_flag(text: str) -> bool:
    return bool(_FLAG_RX.search(text or ""))


def humanize_condition(cond: str, lang: str = "") -> str:
    c = cond.strip()
    if not c:
        return ""
    if len(c) > 220:
        return c[:200] + "…"
    if is_feature_flag(c) and not re.search(r"(?:isEnabled|is_enabled|IsEnabled|Enabled|boolVariation|BoolVariation|getTreatment|GetTreatment|isOn|IsOn|isFeatureEnabled|variation|Variation|isActive|enabled)\(\s*['\"]", c):
        return "feature flag is on: " + _humanize_expr(c)
    for rx, repl in (_PY_NEG_RULES if lang == "python" else _NEG_RULES):
        c = rx.sub(repl, c)
    for rx, repl in _COND_REPLACEMENTS:
        c = rx.sub(repl, c)
    c = _humanize_expr(c)
    return c.strip()


def _humanize_expr(c: str) -> str:
    # dotted paths -> words: order.total -> "order total"; this./self. dropped
    c = re.sub(r"\b(this|self)\.", "", c)
    c = re.sub(r"\b(req|ctx)\.(?!(?:body|params|query|headers|user|method|path)\b)", "", c)
    c = re.sub(r"\b(req|request|ctx)\.(body|params|query|user)\.", "", c)
    c = re.sub(r"\b(\w+)\?\.", r"\1.", c)
    c = re.sub(r"[A-Z][A-Z0-9_]+\.([A-Z][A-Z0-9_]+)\b", lambda m: m.group(1).lower().replace("_", " "), c)     # Status.CANCELLED -> cancelled
    c = re.sub(r"\b([A-Z]\w*)\.([A-Z][A-Z0-9_]+)\b", lambda m: m.group(2).lower().replace("_", " "), c)
    c = re.sub(r"\b[a-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+\b", lambda m: " ".join(humanize(p) for p in m.group(0).split(".")), c)
    c = re.sub(r"\b[a-z][a-z0-9]*[A-Z]\w*\b", lambda m: humanize(m.group(0)), c)
    c = re.sub(r"\b([a-z]+)_([a-z_]+)\b", lambda m: humanize(m.group(0)), c)
    c = re.sub(r"[\"']", "", c)
    c = re.sub(r"\(\s*\)", "", c)
    c = re.sub(r"\s+", " ", c)
    c = re.sub(r"^\((.*)\)$", r"\1", c)
    return c.strip()


def branch_label(arm_label: str, cond: str, lang: str = "", subject: str = "") -> str:
    if arm_label == "if":
        return sentence("if " + humanize_condition(cond, lang))
    if arm_label == "else if":
        return sentence("else if " + humanize_condition(cond, lang))
    if arm_label == "else":
        return "Otherwise"
    if arm_label == "default":
        return "Otherwise"
    if arm_label.startswith("case "):
        val = arm_label[5:]
        val = re.sub(r"\b[A-Z]\w*\.([A-Z][A-Z0-9_]+)\b", r"\1", val)
        val = re.sub(r"[\"']", "", val)
        subj = _humanize_expr(re.sub(r"\.get(\w+)\(\)", lambda m: "." + m.group(1)[0].lower() + m.group(1)[1:], subject)) if subject else ""
        return sentence(f"when {subj} is {val}") if subj else sentence(f"when {val}")
    if arm_label == "try":
        return "Try"
    if arm_label.startswith("catch"):
        exc = arm_label[5:].strip()
        first = exc.split("|")[0].strip()
        if not first or re.match(r"^[a-z_]\w*$", first) or first in ("Exception", "Error", "Throwable", "error", "err", "e", "ex"):
            return "On any error"
        return sentence("on " + (error_words(first) or "any error"))
    if arm_label == "finally":
        return "Finally, always"
    return sentence(arm_label)


# ---------------------------------------------------------------------------- calls

_DB_VERBS = [
    (re.compile(r"^(findById|getById|findOne|getOne|getReferenceById|findByPk|findByPrimaryKey|find_one|get_object_or_404|FindByID|GetByID|First|Take|findUnique|findFirst|get_by_id|retrieve|getItem|get_item|GetItem)$", re.I), "Load {entity} by ID"),
    (re.compile(r"^(findAll|getAll|list|listAll|find_all|findMany|all|scan|Scan|find_many)$", re.I), "Load all {entities}"),
    (re.compile(r"^(find|findBy|findAllBy|get|getBy|query|queryBy|search|filter|select|Find|where|Where|fetch|fetchAll|fetchBy|read|lookup|exists|existsBy|count|countBy|aggregate|queryForList|queryForObject|queryForMap|selectOne|selectList|selectMany|QueryRow|QueryContext|Query|list_by|find_by|get_by|search_by|Raw|Exec)(\w*)$"), None),
    (re.compile(r"^(save|saveAll|saveAndFlush|persist|merge|insert|insertOne|insertMany|create|createOne|createMany|add|put|putItem|put_item|PutItem|Create|Save|bulk_create|batchWrite|batch_write|store|upsert|Upsert|update|updateOne|updateMany|updateById|update_one|Update|UpdateItem|update_item|Updates|Set|set|delete|deleteById|deleteAll|remove|deleteOne|deleteMany|destroy|Delete|DeleteItem|delete_item|Remove|patch)(\w*)$"), None),
    (re.compile(r"^(commit|rollback|begin|beginTransaction|transaction|atomic|Begin|Commit|Rollback|withTransaction|runInTransaction|\$transaction|startSession)(\w*)$", re.I), None),
]

_WRITE_WORDS = {"save": "Save", "saveall": "Save all", "saveandflush": "Save", "persist": "Insert", "merge": "Update", "insert": "Insert", "insertone": "Insert",
                "insertmany": "Insert", "create": "Create", "createone": "Create", "createmany": "Create", "add": "Insert", "put": "Store", "putitem": "Store",
                "put_item": "Store", "bulk_create": "Insert", "batchwrite": "Store", "batch_write": "Store", "store": "Store", "upsert": "Upsert",
                "update": "Update", "updateone": "Update", "updatemany": "Update", "updatebyid": "Update", "update_one": "Update", "updateitem": "Update",
                "update_item": "Update", "updates": "Update", "set": "Update", "delete": "Delete", "deletebyid": "Delete", "deleteall": "Delete all",
                "remove": "Delete", "deleteone": "Delete", "deletemany": "Delete", "destroy": "Delete", "deleteitem": "Delete", "delete_item": "Delete", "patch": "Update"}
_TX_WORDS = {"commit": "Commit transaction", "rollback": "Roll back transaction", "begin": "Begin transaction", "begintransaction": "Begin transaction",
             "transaction": "Run in a transaction", "atomic": "Run in a transaction", "withtransaction": "Run in a transaction",
             "runintransaction": "Run in a transaction", "$transaction": "Run in a transaction", "startsession": "Start a database session"}


def db_label(method: str, entity: str, receiver_words: str, args: str = "", context: str = "") -> str:
    entity = humanize(entity) if entity else ""
    ent = singular(entity) if entity else ""
    ents = (entity if entity.endswith("s") else entity + "s") if entity else ""
    # SQLAlchemy-style chains: session.query(Order).filter(Order.id == x).first()
    if method in ("query", "select", "find", "filter", "where", "objects") and context:
        single = re.search(r"\.(first|one|one_or_none|get|scalar|first_or_404|get_or_404|FirstOrDefault|SingleOrDefault|First|Single|Take\(1\))\(", context)
        by_id = re.search(r"\.(?:id|pk|uuid|key)\s*==|\bid\s*=|\bpk\s*=|\.get\(", context)
        if single:
            return f"Load {ent or 'record'}" + (" by ID" if by_id else "")
        if re.search(r"\.(filter|where|filter_by|Where)\(", context):
            return f"Query {ents or 'records'}"
    if not ent:
        # derive from receiver: orderRepository -> order
        rw = receiver_words.replace("repository", "").replace("repo", "").replace("dao", "").replace("mapper", "").replace("store", "").replace("model", "").strip()
        ent = singular(rw) if rw else "record"
        ents = ent + "s" if not ent.endswith("s") else ent
    low = method.lower()
    sql = re.search(r"['\"`]\s*(select|insert|update|delete|merge|call|with)\b", args, re.I)
    if sql:
        verb = sql.group(1).upper()
        table = re.search(r"\b(?:from|into|update|join)\s+[`\"]?(\w+)", args, re.I)
        return f"Run SQL {verb}" + (f" on {table.group(1)}" if table else "")
    if low in _TX_WORDS:
        return _TX_WORDS[low]
    for rx, template in _DB_VERBS[:2]:
        if rx.match(method):
            return template.format(entity=ent, entities=ents)
    m = re.match(r"^(find|get|query|search|filter|select|fetch|read|lookup|exists|count|list|Find|Get|First|Take|Where|find_by|get_by|list_by|search_by)(?:All)?(?:By|_by|_)?(.*)$", method)
    if m:
        verb = m.group(1).lower()
        crit = m.group(2)
        crit_words = humanize(re.sub(r"(OrderBy\w*|Asc|Desc|IgnoreCase)$", "", crit)).replace(" and ", " and ") if crit else ""
        crit_words = re.sub(r"\band\b", "and", crit_words)
        if verb in ("exists",):
            return f"Check whether {ent} exists" + (f" by {crit_words}" if crit_words else "")
        if verb == "count":
            return f"Count {ents}" + (f" by {crit_words}" if crit_words else "")
        head = "Load" if verb in ("get", "find", "fetch", "read", "lookup", "first", "take", "get_by", "find_by") else "Query"
        if crit_words:
            if head == "Load" and not re.match(r"^(id|ID|pk|uuid|UUID|key)$", crit_words.strip()):
                head = "Query"   # findByCustomerId may return many rows
            return f"{head} {ents if head == 'Query' else ent} by {crit_words}"
        return f"{head} {ents}" if head == "Query" else f"Load {ent}"
    if low in _WRITE_WORDS:
        return f"{_WRITE_WORDS[low]} {ent}"
    for prefix, word in (("save", "Save"), ("insert", "Insert"), ("update", "Update"), ("delete", "Delete"), ("remove", "Delete"), ("create", "Create"), ("upsert", "Upsert")):
        if low.startswith(prefix):
            return f"{word} {ent}" + (f" {humanize(method[len(prefix):])}" if len(method) > len(prefix) else "")
    return f"Database: {humanize(method)} {ent}".strip()


def http_label(service: str, verb_path: str, method: str, args: str = "") -> str:
    url = re.search(r"['\"`](https?://[^'\"`\s]+|/[^'\"`\s]*)", args or "")
    if verb_path:
        return f"Call {service}: {verb_path}".strip()
    tpl = re.search(r"`\$\{\s*([A-Za-z_][\w.]*)\s*\}(/[^`$]*)?", args or "") or re.search(r"\bf['\"]\{\s*([A-Za-z_][\w.]*)\s*\}(/[^'\"{]*)?", args or "") \
        or re.search(r"\$['\"]\{\s*([A-Za-z_][\w.]*)\s*\}(/[^'\"{]*)?", args or "")
    if tpl:
        var = tpl.group(1).split(".")[-1]
        var = re.sub(r"(?i)(_?url|_?uri|_?base_?url|_?endpoint|_?host|_?base)$", "", var)
        service = (humanize(var) + " service") if var else service
        path_part = (tpl.group(2) or "").split("$")[0].rstrip("/")
        verb = next((v.upper() for v in ("get", "post", "put", "patch", "delete") if method.lower().startswith(v) or method.lower().endswith(v)), "")
        return f"Call {service}: {verb} {path_part}".replace("  ", " ").strip().rstrip(":")
    if not service:
        vm = re.search(r"\b([A-Za-z_]\w*?)(?:Url|URL|Uri|URI|BaseUrl|BaseURL|Endpoint|Host|Base)\b\s*\+", args or "")
        if vm and vm.group(1):
            service = humanize(vm.group(1)) + " service"
            pm = re.search(r"\+\s*['\"](/[^'\"]*)", args or "")
            verb = next((v.upper() for v in ("get", "post", "put", "patch", "delete") if method.lower().startswith(v) or method.lower().endswith(v)), "")
            if pm:
                return f"Call {service}: {verb} {pm.group(1)}".replace("  ", " ").strip()
    verb = ""
    ml = method.lower()
    for v in ("get", "post", "put", "patch", "delete", "head", "options"):
        if ml.startswith(v) or ml.endswith(v):
            verb = v.upper()
            break
    if url:
        u = url.group(1)
        u = re.sub(r"^https?://", "", u)
        return f"Call {u}" + (f" ({verb})" if verb else "")
    if service:
        return f"Call {service}" + (f": {verb}" if verb else f": {humanize(method)}")
    return f"Call external service: {verb or humanize(method)}"


_BROKERS = [("kafka", "Kafka"), ("sqs", "SQS"), ("sns", "SNS"), ("rabbit", "RabbitMQ"), ("amqp", "RabbitMQ"), ("jms", "JMS"),
            ("eventbridge", "EventBridge"), ("event bridge", "EventBridge"), ("kinesis", "Kinesis"), ("pubsub", "Pub/Sub"), ("pub sub", "Pub/Sub"),
            ("nats", "NATS"), ("servicebus", "Service Bus"), ("service bus", "Service Bus"), ("pulsar", "Pulsar"), ("celery", "Celery"),
            ("activemq", "ActiveMQ"), ("stream bridge", "the event stream"), ("event publisher", "the event bus"), ("mediator", "the mediator")]


def queue_label(method: str, receiver_words: str, args: str = "") -> str:
    ml = method.lower()
    target = ""
    rw = (receiver_words or "").lower()
    for key, name in _BROKERS:
        if key in rw:
            receiver_words = name
            break
    else:
        receiver_words = ""
    a = args or ""
    lits = [x for x in re.findall(r"['\"`]([\w.\-/:]+)['\"`]", a) if not re.match(r"^(ORDER_|EVENT_)?[A-Z_]+$", x) or "-" in x or "." in x]
    lits = [x for x in lits if not x.startswith("X-") and not x.startswith("application/")]
    if lits:
        target = lits[0]
        if target.startswith("arn:"):
            target = target.split(":")[-1]
        elif "://" in target:
            target = target.rstrip("/").split("/")[-1]
    else:
        m = re.search(r"\b([A-Z][A-Z0-9_]*(?:TOPIC|QUEUE|EVENT|STREAM|CHANNEL)(?!_URL|_ARN)[A-Z0-9_]*|\w*(?:Topic|Queue|Stream|Channel)(?!Url|Arn)\w*)\b", a)
        if m:
            target = humanize(m.group(1))
    event = ""
    em = re.search(r"\b(?:type|eventType|event_type|name|detailType|DetailType)\s*[:=]\s*['\"]([\w.\-]+)['\"]", a)
    if em:
        event = em.group(1)
    else:
        em = re.search(r"\bnew\s+([A-Z]\w*(?:Event|Message|Command|Notification))\b|\b([A-Z]\w*(?:Event|Message|Command))\s*\(", a)
        if em and not re.match(r"^(Send|Publish|Put|Get|Delete|Receive|Create|Batch)\w*(Command|Request|Message)$", em.group(1) or em.group(2)):
            event = em.group(1) or em.group(2)
    what = f"{event}" if event else "message"
    tgt = f" '{target}'" if target else (f" {receiver_words}" if receiver_words else "")
    via = f" via {receiver_words}" if target and receiver_words else ""
    if any(k in ml for k in ("consume", "receive", "poll", "subscribe", "listen", "read")):
        return f"Read messages from{tgt}{via}" if tgt else "Read messages from queue"
    if any(k in ml for k in ("publish", "send", "emit", "produce", "put", "push", "enqueue", "dispatch", "notify", "trigger", "fire", "delay", "apply_async")):
        return f"Publish {what} to{tgt}{via}" if tgt else f"Publish {what}"
    return f"Message queue: {humanize(method)}{tgt}"


def cache_label(method: str, args: str = "") -> str:
    ml = method.lower()
    key = re.search(r"['\"`]([\w.:\-{}$]+)['\"`]", args or "")
    k = f" '{key.group(1)}'" if key else ""
    if any(x in ml for x in ("evict", "del", "remove", "invalidate", "clear", "expire", "flush")):
        return f"Evict cache entry{k}"
    if any(x in ml for x in ("set", "put", "write", "store", "cache")):
        return f"Write to cache{k}"
    if any(x in ml for x in ("get", "read", "fetch", "lookup", "has", "exists", "mget", "hget")):
        return f"Read from cache{k}"
    return f"Cache: {humanize(method)}{k}"


_ROLE_WORDS = re.compile(r"\b(service|services|impl|manager|handler|handlers|controller|controllers|repository|repo|client|clients|facade|helper|helpers|util|utils|provider|processor|orchestrator|coordinator|gateway|adapter|component|bean|module|api|core|logic|domain|use case|usecase|interactor|worker|job|task|engine|resolver|resolvers)\b")


def call_label(method: str, owner: str = "", receiver_var: str = "", resolved: bool = True) -> str:
    words = humanize(method)
    if not words:
        words = "call"
    where = type_words(owner) if owner else humanize(receiver_var)
    # "getOrder" -> "Get order"; add the collaborator only when it adds meaning
    if where and not resolved:
        return sentence(f"call {where}: {words}")
    # single-verb methods read better with the owner's noun: OrderService.cancel -> "Cancel order"
    if resolved and len(split_words(method)) == 1 and where:
        noun = _ROLE_WORDS.sub("", where).strip()
        noun = re.sub(r"\s+", " ", noun)
        if noun and noun.lower() not in words.lower() and len(noun.split()) <= 2:
            return sentence(f"{words} {singular(noun)}")
    return sentence(words)


def error_words(exc: str) -> str:
    w = type_words(exc)
    w = re.sub(r"\b(exception|error|fault|failure)\b", "", w).strip()
    return w


def return_words_from_type(t: str) -> str:
    """ResponseEntity<List<OrderDto>> -> 'list of order DTOs'; Mono<Void> -> ''."""
    if not t:
        return ""
    t = t.strip()
    wrappers = ("ResponseEntity", "Response", "Mono", "Flux", "CompletableFuture", "Future", "Task", "Promise", "ActionResult",
                "IActionResult", "Optional", "Deferred", "HttpEntity", "Result", "ApiResponse", "Awaitable", "Coroutine", "Callable", "DeferredResult", "ServerResponse")
    plural = False
    while True:
        m = re.match(r"^(\w+)\s*<\s*(.+)\s*>$", t)
        if not m:
            break
        outer, inner = m.group(1), m.group(2)
        if outer in ("List", "Collection", "Iterable", "Set", "Array", "Page", "Slice", "Stream", "IEnumerable", "IList", "Sequence", "Seq", "Flux", "Observable", "ReadonlyArray"):
            plural = True
        elif outer not in wrappers:
            break
        t = inner.strip()
    t = t.rstrip("?")
    if "," in t:   # Go: (*store.Order, error)
        t = t.strip("()").split(",")[0].strip()
    t = t.lstrip("*&").strip("()")
    if t in ("byte[]", "[]byte", "Byte[]"):
        return "binary content"
    if t.startswith("[]"):
        plural, t = True, t[2:].lstrip("*")
    if t.endswith("[]"):
        plural, t = True, t[:-2]
    t = t.split(".")[-1]
    if t in ("byte[]", "Byte[]", "ByteArray", "bytes", "Buffer", "InputStreamResource", "Resource", "StreamingResponseBody", "Stream", "FileResponse", "StreamingResponse", "Blob", "ReadableStream", "byte", "Byte"):
        return "binary content"
    if t in ("Void", "void", "Unit", "None", "Any", "Object", "Map", "Dict", "dict", "bool", "boolean", "Boolean", "String", "string", "str",
             "int", "Integer", "long", "Long", "float", "double", "Number", "number", "IActionResult", "ActionResult", "Response", "JsonResponse", "HttpResponse"):
        return ""
    if plural:
        words = humanize(t)
        return "list of " + (words + "s" if not words.lower().endswith("s") else words)
    return humanize(t)


def return_label(status: Optional[int], expr: str, depth: int, lang: str, return_type: str = "") -> str:
    body = return_words_from_type(return_type) or _return_body_words(expr)
    if status:
        s = status_label(status)
        return f"Respond {s}" + (f" with {body}" if body and status < 300 else "")
    if depth == 0:
        return "Respond with " + body if body else "Respond"
    return "Return " + body if body else "Return"


def _return_body_words(expr: str) -> str:
    e = (expr or "").strip()
    if not e or e in ("None", "null", "nil", "undefined", "void", "Unit"):
        return ""
    if re.search(r"\.(sendStatus|end|SendStatus|NoContent|noContent|WriteHeader|Status|status)\(\s*[\w.]*\s*\)\s*(?:\.(end|build|send)\(\))?\s*$", e) and not re.search(r"\.(json|send|body|JSON|String)\(", e):
        return ""
    if re.search(r"\bhttp\.Error\(|AbortWithStatus", e):
        return ""
    e = re.sub(r"^(?:ResponseEntity|Response|Ok|Results|TypedResults|NextResponse|JSONResponse|JsonResponse|jsonify|res\.json|res\.send|reply\.send|c\.JSON|c\.json)\s*[.(]\s*(?:ok|json|status|of|body|created|accepted|noContent|Ok|Json)?\s*\(?", "", e)
    e = re.sub(r"\)\s*\.build\(\)\s*$", "", e)
    inner = re.findall(r"\(([^()]*)\)", e)
    if inner:
        cand_words = [w for grp in inner for w in re.findall(r"[A-Za-z_$][\w$]*", grp.split(",")[0])]
        cand_words = [w for w in cand_words if w not in ("null", "None", "true", "false", "nil", "undefined", "await", "new", "this", "self")]
        if cand_words:
            return humanize(cand_words[-1])
    e = re.sub(r"[()]", " ", e)
    e = re.sub(r"\bnew\b|\bawait\b", " ", e)
    words = re.findall(r"[A-Za-z_$][\w$]*", e)
    if not words:
        return ""
    # prefer the variable/type name, not the mapper method
    cand = words[0]
    for w in words:
        if w not in ("mapper", "toDto", "toResponse", "map", "of", "this", "self", "res", "ctx", "status", "body", "json", "data", "response", "Mono", "Flux", "ok", "just", "build"):
            cand = w
            break
    return humanize(cand)


_GENERIC_HTTP_EXC = {"HTTPException", "HttpException", "ResponseStatusException", "HttpError", "HTTPError", "ApiError", "APIError", "ApiException",
                     "Boom", "HttpResponseException", "WebApplicationException", "ClientErrorException", "ServerErrorException", "HttpStatusCodeException",
                     "HttpClientErrorException", "HttpServerErrorException", "createError", "abort", "StatusCodeError", "HttpStatusException", "RestException", "Exception"}


def throw_label(exc: str, status: Optional[int], message: str = "") -> str:
    exc = (exc or "").split(".")[-1] if " " not in (exc or "") else exc
    if " " in exc:   # Go: errors.New("order must contain items")
        return f"Fail: {exc}" + (f" ({status_label(status)})" if status else "")
    generic = exc in _GENERIC_HTTP_EXC or not exc
    name = type_words(re.sub(r"^Err(?=[A-Z])", "", exc)) if exc else "error"
    name = name.replace(" exception", "").replace(" error", "").strip() or "error"
    name = {"value": "invalid value", "type": "type error", "key": "missing key", "index": "index error", "runtime": "runtime error",
            "illegal state": "illegal state", "assertion": "assertion failed", "attribute": "attribute error", "lookup": "lookup failed"}.get(name, name)
    msg = message.strip().strip("\"'")
    tail = f" — {msg[:60]}" if msg and len(msg) < 60 else ""
    if status and generic:
        return f"Fail with {status_label(status)}{tail}"
    if status:
        return f"Fail: {name} ({status_label(status)}){tail}"
    return f"Fail with {name}{tail}"


def loop_label(word: str, cond: str, lang: str) -> str:
    c = cond.strip()
    m = re.match(r"^(?:final\s+)?(?:[\w.<>\[\]]+\s+)?(\w+)\s*(?::|in|of|:=\s*range|=\s*range)\s*(.+)$", c)
    if m:
        item, coll = m.group(1), m.group(2)
        coll = re.sub(r"^range\s+", "", coll)
        return sentence(f"for each {humanize(item)} in {_humanize_expr(coll)}")
    m = re.match(r"^_?\s*,\s*(\w+)\s*:=\s*range\s+(.+)$", c)
    if m:
        return sentence(f"for each {humanize(m.group(1))} in {_humanize_expr(m.group(2))}")
    if word == "while" or word == "do":
        return sentence("repeat while " + humanize_condition(c, lang)) if c else "Repeat"
    if c:
        return sentence("loop: " + humanize_condition(c, lang)[:80])
    return "Loop"
