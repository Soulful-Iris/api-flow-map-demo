"""Merge human/Claude-written annotations into a model or diff dict.

Annotation file format (JSON):
{
  "digest": "Two sentences a reviewer should read first (markdown-lite: **bold**, `code`, line breaks).",
  "endpoints": {
    "POST /api/v1/orders": {
      "title": "Place an order",
      "summary": "Reserves stock, charges the card, publishes ORDER_PLACED.",
      "changes_summary": "Fraud review now triggers at score > 60 and sends a confirmation email behind a flag.",
      "steps": { "<step id>": "Reserve stock with inventory-service" }
    }
  }
}
Step ids are the structural ids from the model (`explain` prints them). Unknown
ids are ignored so stale annotations never break a render.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List


def load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("annotations must be a JSON object")
    return data


def apply(data: Dict[str, Any], ann: Dict[str, Any]) -> Dict[str, Any]:
    """Mutates and returns `data` (a model dict or a diff dict)."""
    if not ann:
        return data
    if ann.get("digest"):
        data["digest"] = str(ann["digest"])
    per_ep: Dict[str, Any] = ann.get("endpoints") or {}
    applied = 0
    for ep in data.get("endpoints", []):
        a = per_ep.get(ep.get("id"))
        if not a and ep.get("before", {}).get("id"):
            a = per_ep.get(ep["before"]["id"])
        if not isinstance(a, dict):
            continue
        if a.get("title"):
            ep["auto_title"] = ep.get("auto_title") or ep.get("title", "")
            ep["title"] = str(a["title"])
        if a.get("summary"):
            ep["summary"] = str(a["summary"])
        if a.get("changes_summary"):
            ep["changes_summary"] = str(a["changes_summary"])
        steps: Dict[str, Any] = a.get("steps") or {}
        if steps:
            applied += _apply_steps(ep.get("flow", []), steps)
    data.setdefault("annotations", {})["applied_step_labels"] = applied
    return data


def _apply_steps(nodes: List[Dict[str, Any]], labels: Dict[str, Any]) -> int:
    n = 0
    for node in nodes:
        sid = node.get("id")
        if sid in labels:
            val = labels[sid]
            node["auto_label"] = node.get("auto_label") or node.get("label", "")
            if isinstance(val, dict):
                if val.get("label"):
                    node["label"] = str(val["label"])
                if val.get("summary"):
                    node["summary"] = str(val["summary"])
            else:
                node["label"] = str(val)
            n += 1
        n += _apply_steps(node.get("children", []), labels)
        for b in node.get("branches", []):
            n += _apply_steps(b.get("steps", []), labels)
    return n
