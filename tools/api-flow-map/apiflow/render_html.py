"""Render a model or a diff as a single self-contained HTML file.

No CDN, no external fonts, no network: the file can be attached to a PR,
dropped in Confluence or opened from a CI artifact. All rendering happens
client-side from the embedded JSON so search, filters, expand/collapse and the
title/code toggle work offline.
"""
from __future__ import annotations

import html
import json
from typing import Any, Dict


def _normalize(data: Dict[str, Any], mode: str) -> Dict[str, Any]:
    out = dict(data)
    out["mode"] = mode
    if mode == "scan":
        for ep in out.get("endpoints", []):
            ep.setdefault("status", "unchanged")
            ep.setdefault("risk", "none")
            ep.setdefault("risk_reasons", [])
            ep.setdefault("property_changes", [])
            ep.setdefault("flow_changes", [])
            ep.setdefault("counts", {"added": 0, "removed": 0, "modified": 0})
    return out


def render(data: Dict[str, Any], mode: str = "scan") -> str:
    payload = json.dumps(_normalize(data, mode), ensure_ascii=False).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    repo = (data.get("repo") or {}).get("name") or "API"
    title = f"{repo} — API flow {'diff' if mode == 'diff' else 'map'}"
    return TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__DATA__", payload)


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --bg:#F4F6F8;--surface:#FFFFFF;--ink:#1B2430;--muted:#5C6B7A;--faint:#8A96A3;--line:#DFE3E8;--line-strong:#C9D1DA;
  --accent:#0F6E9E;--accent-soft:#E6F1F7;
  --added:#1E7F4F;--added-bg:#E8F5EE;--removed:#B42318;--removed-bg:#FCEBEA;--modified:#B25E09;--modified-bg:#FFF3E0;--moved:#6B5CA5;--moved-bg:#EFEBF8;
  --get:#0F6E9E;--post:#1E7F4F;--put:#B25E09;--patch:#7C4DB3;--delete:#B42318;--any:#5C6B7A;
  --rail:#7C6F9B;--ok:#1E7F4F;--warn:#B25E09;--bad:#B42318;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.45}
a{color:var(--accent)}
button{font:inherit;color:inherit;background:none;border:1px solid var(--line-strong);border-radius:6px;padding:4px 10px;cursor:pointer}
button:hover{background:var(--accent-soft);border-color:var(--accent)}
button.on{background:var(--ink);color:#fff;border-color:var(--ink)}
input[type=search]{font:inherit;padding:6px 10px;border:1px solid var(--line-strong);border-radius:6px;width:100%;background:var(--surface)}
kbd{font-family:var(--mono);font-size:11px;border:1px solid var(--line-strong);border-bottom-width:2px;border-radius:4px;padding:0 5px;color:var(--muted)}
.app{display:grid;grid-template-columns:340px 1fr;grid-template-rows:auto 1fr;height:100vh}
header.top{grid-column:1/3;display:flex;align-items:center;gap:18px;padding:12px 20px;background:var(--surface);border-bottom:1px solid var(--line)}
header.top h1{font-size:16px;margin:0;font-weight:650;letter-spacing:-.01em}
header.top .sub{color:var(--muted);font-size:12.5px}
header.top .refs{font-family:var(--mono);font-size:12px;color:var(--muted);display:flex;gap:8px;align-items:center}
header.top .refs b{color:var(--ink);font-weight:600}
header.top .spacer{flex:1}
.pill{display:inline-flex;align-items:center;gap:6px;padding:2px 9px;border-radius:999px;font-size:12px;border:1px solid var(--line);background:var(--surface);white-space:nowrap}
.pill.added{color:var(--added);border-color:#B7E1C8;background:var(--added-bg)}
.pill.removed{color:var(--removed);border-color:#F1B4AE;background:var(--removed-bg)}
.pill.modified{color:var(--modified);border-color:#F3D3A6;background:var(--modified-bg)}
.pill.risk-high{color:#fff;background:var(--bad);border-color:var(--bad)}
.pill.risk-medium{color:#fff;background:var(--warn);border-color:var(--warn)}
.pill.risk-low{color:var(--ink);background:#E9ECEF;border-color:var(--line-strong)}
.pill.risk-none{color:var(--muted)}
aside.rail{border-right:1px solid var(--line);background:var(--surface);overflow:auto;display:flex;flex-direction:column}
.rail .tools{padding:12px 14px;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:8px;position:sticky;top:0;background:var(--surface);z-index:2}
.rail .toggles{display:flex;gap:6px;flex-wrap:wrap}
.rail .toggles button{padding:3px 9px;font-size:12px}
.rail .group{padding:10px 0 4px}
.rail .group h3{margin:0;padding:6px 14px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);font-weight:600}
.ep{display:grid;grid-template-columns:52px 1fr;gap:8px;align-items:start;padding:8px 14px;cursor:pointer;border-left:3px solid transparent}
.ep .tline{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.ep:hover{background:var(--bg)}
.ep.sel{background:var(--accent-soft);border-left-color:var(--accent)}
.ep .path{font-family:var(--mono);font-size:12px;color:var(--ink);overflow-wrap:anywhere}
.ep .path .param{color:var(--accent)}
.ep .t{font-size:12.5px;color:var(--muted);margin-top:1px}
.ep.st-removed .path,.ep.st-removed .t{text-decoration:line-through;color:var(--faint)}
.ep .badges{display:flex;align-items:center;gap:5px;flex:none}
.m{display:inline-block;font-family:var(--mono);font-size:10.5px;font-weight:700;letter-spacing:.02em;padding:2px 0;border-radius:4px;text-align:center;width:52px;color:#fff;background:var(--any)}
.m.GET{background:var(--get)}.m.POST{background:var(--post)}.m.PUT{background:var(--put)}.m.PATCH{background:var(--patch)}.m.DELETE{background:var(--delete)}
.chg{font-family:var(--mono);font-size:11px;font-weight:700;padding:1px 6px;border-radius:4px}
.chg.added{color:var(--added);background:var(--added-bg)}.chg.removed{color:var(--removed);background:var(--removed-bg)}.chg.modified{color:var(--modified);background:var(--modified-bg)}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.dot.high{background:var(--bad)}.dot.medium{background:var(--warn)}.dot.low{background:#9AA5B1}.dot.none{background:transparent;border:1px solid var(--line-strong)}
main{overflow:auto;padding:22px 30px 60px}
.digest{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;padding:14px 18px;margin-bottom:18px;max-width:1100px}
.digest h2{margin:0 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600}
.digest .text{font-size:14.5px;line-height:1.55;white-space:pre-wrap}
.digest ul{margin:8px 0 0;padding-left:18px}
.digest li{margin:3px 0}
.digest li .ep-link{font-family:var(--mono);font-size:12px;color:var(--accent);cursor:pointer;text-decoration:underline dotted}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;max-width:1100px}
.card .head{padding:18px 22px 14px;border-bottom:1px solid var(--line)}
.card .head .route{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.card .head .route .p{font-family:var(--mono);font-size:15px;font-weight:600}
.card .head .route .p .param{color:var(--accent)}
.card .head .route .p.was{color:var(--faint);text-decoration:line-through;font-weight:400;font-size:13px}
.card .head h2{margin:8px 0 2px;font-size:22px;font-weight:650;letter-spacing:-.015em}
.card .head h2 .auto{font-weight:400;color:var(--faint);font-size:14px;margin-left:8px}
.card .head .summary{color:var(--muted);font-size:14px;max-width:820px}
.card .head .handler{font-family:var(--mono);font-size:12px;color:var(--faint);margin-top:6px}
.props{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.prop{display:inline-flex;align-items:baseline;gap:6px;font-size:12px;padding:3px 9px;border-radius:6px;background:var(--bg);border:1px solid var(--line)}
.prop b{font-weight:600;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.prop.added{background:var(--added-bg);border-color:#B7E1C8}.prop.removed{background:var(--removed-bg);border-color:#F1B4AE}.prop.modified{background:var(--modified-bg);border-color:#F3D3A6}
.prop .was{color:var(--faint);text-decoration:line-through;margin-right:4px}
.warn{margin-top:10px;font-size:12.5px;color:var(--warn);background:var(--modified-bg);border:1px solid #F3D3A6;border-radius:6px;padding:6px 10px}
.changes{padding:14px 22px;border-bottom:1px solid var(--line);background:#FBFCFD}
.changes h3{margin:0 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600}
.changes ol{margin:0;padding-left:0;list-style:none}
.changes li{display:flex;gap:10px;align-items:baseline;padding:4px 0;font-size:13.5px;cursor:pointer}
.changes li:hover .reason{text-decoration:underline}
.changes li .crumb{font-size:12px;color:var(--faint)}
.changes li .sev{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;width:58px;flex:none}
.sev.high{color:var(--bad)}.sev.medium{color:var(--warn)}.sev.low{color:#7B8794}
.changes .csum{font-size:14px;margin:0 0 10px;color:var(--ink)}
.flow{padding:12px 22px 22px}
.flow .bar{display:flex;gap:8px;align-items:center;margin:4px 0 14px;font-size:12px;color:var(--muted)}
.flow .bar button{font-size:12px;padding:3px 9px}
.ledger{position:relative}
.row{display:grid;grid-template-columns:22px 26px 1fr;align-items:start;position:relative;min-height:30px}
.row .gut{font-family:var(--mono);font-size:13px;font-weight:700;text-align:center;padding-top:5px;color:var(--faint);user-select:none}
.row .spine{position:relative;height:100%}
.row .spine:before{content:"";position:absolute;left:12px;top:0;bottom:0;width:2px;background:var(--line-strong)}
.row.first .spine:before{top:14px}
.row.last .spine:before{bottom:calc(100% - 16px)}
.row .glyph{position:absolute;left:5px;top:7px;width:16px;height:16px;border-radius:50%;background:var(--surface);border:2px solid var(--line-strong);display:flex;align-items:center;justify-content:center;font-size:9px;line-height:1;color:var(--muted);font-family:var(--mono)}
.row .body{padding:5px 8px 5px 6px;border-radius:6px;margin:1px 0}
.row .lbl{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.row .lbl .txt{font-size:14px}
.row .lbl .txt.bold{font-weight:600}
.row .lbl .meta{display:inline-flex;gap:5px;align-items:center;flex-wrap:wrap}
.row .lbl .meta .tag{font-size:10.5px;padding:1px 6px;border-radius:4px;background:var(--bg);border:1px solid var(--line);color:var(--muted);font-family:var(--mono)}
.row .lbl .meta .tag.flag{color:var(--patch);border-color:#D9CCEE;background:#F3EEFA}
.row .lbl .meta .tag.exit{color:var(--muted)}
.st{font-family:var(--mono);font-size:11px;font-weight:700;padding:1px 6px;border-radius:4px;color:#fff;background:var(--ok);display:inline-block;line-height:1.4}
.st.s3{background:var(--accent)}.st.s4{background:var(--warn)}.st.s5{background:var(--bad)}
.prop .st{margin-right:3px}
.row .lbl .meta .thr{font-family:var(--mono);font-size:11px;color:var(--bad)}
.row .lbl .loc{font-family:var(--mono);font-size:11px;color:var(--faint);margin-left:auto;white-space:nowrap}
.row .code{font-family:var(--mono);font-size:12px;color:var(--muted);margin-top:3px;white-space:pre-wrap;word-break:break-all;display:none}
.showcode .row .code{display:block}
.row .was{font-size:12.5px;color:var(--removed);text-decoration:line-through;margin-top:2px}
.row .was b{font-weight:500;text-decoration:none;color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-right:6px}
.row .sumline{font-size:12.5px;color:var(--muted);margin-top:2px}
.row.added>.body{background:var(--added-bg)}.row.added>.gut{color:var(--added)}
.row.removed>.body{background:var(--removed-bg)}.row.removed>.gut{color:var(--removed)}.row.removed>.body .txt{text-decoration:line-through;color:#8C3A33}
.row.modified>.body{background:var(--modified-bg)}.row.modified>.gut{color:var(--modified)}
.row.moved>.gut{color:var(--moved)}.row.moved>.body{background:var(--moved-bg)}
.row.moved-from{display:none}
.kids{grid-column:3/4;margin-left:4px;border-left:1px dashed var(--line-strong);padding-left:6px}
.kids.collapsed{display:none}
.exp{font-family:var(--mono);font-size:11px;color:var(--accent);cursor:pointer;border:1px solid transparent;padding:0 5px;border-radius:4px}
.exp:hover{border-color:var(--accent)}
.branch{grid-column:3/4;margin:2px 0 4px 4px;border-left:2px solid var(--rail);border-radius:0 0 0 6px;padding-left:8px}
.branch .arm{display:flex;align-items:baseline;gap:8px;padding:3px 6px;border-radius:5px;margin:2px 0}
.branch .arm .diamond{color:var(--rail);font-size:12px}
.branch .arm .alabel{font-size:13.5px;font-weight:600;color:#4E4372}
.branch .arm .exit{font-size:10.5px;color:var(--faint);font-family:var(--mono);border:1px solid var(--line);border-radius:4px;padding:0 5px}
.branch .arm.added{background:var(--added-bg)}.branch .arm.removed{background:var(--removed-bg)}.branch .arm.removed .alabel{text-decoration:line-through;color:#8C3A33}.branch .arm.modified{background:var(--modified-bg)}
.branch .arm .awas{font-size:12px;color:var(--removed);text-decoration:line-through}
.branch .armsteps{margin-left:6px}
.branch .empty{font-size:12px;color:var(--faint);padding:2px 12px 6px;font-style:italic}
.kind-log>.body{opacity:.75}
.hidekind-log .kind-log{display:none}
.hidekind-transform .kind-transform{display:none}
.g-call{border-color:#9AA5B1}.g-io\.db{border-color:#0F6E9E;color:#0F6E9E}.g-io\.http{border-color:#1E7F4F;color:#1E7F4F}.g-io\.queue{border-color:#7C4DB3;color:#7C4DB3}.g-io\.cache{border-color:#B25E09;color:#B25E09}.g-io\.file{border-color:#5C6B7A}
.g-branch{border-color:var(--rail);color:var(--rail);border-radius:3px !important;transform:rotate(45deg)}.g-branch span{transform:rotate(-45deg);display:block}
.g-loop{border-color:var(--rail);color:var(--rail)}.g-try{border-color:var(--warn);color:var(--warn)}
.g-return{border-color:var(--ink);background:var(--ink) !important;color:#fff}.g-throw{border-color:var(--bad);background:var(--bad) !important;color:#fff}
.g-validate{border-color:var(--ok);color:var(--ok)}.g-auth{border-color:var(--patch);color:var(--patch)}.g-transform{border-color:#9AA5B1}.g-log{border-color:var(--line);color:var(--faint)}.g-external{border-color:#9AA5B1;border-style:dashed}.g-note{border-color:var(--line)}
.legend{display:flex;flex-wrap:wrap;gap:10px 16px;margin:14px 0 0;font-size:12px;color:var(--muted)}
.legend .glyph{position:static;display:inline-flex;margin-right:6px;vertical-align:middle}
.empty-state{padding:60px 20px;color:var(--muted);text-align:center;font-size:15px}
.diag{margin-top:18px;max-width:1100px;font-size:12.5px;color:var(--muted)}
.diag summary{cursor:pointer}
.diag ul{margin:6px 0 0;padding-left:18px;font-family:var(--mono);font-size:11.5px}
.help{position:fixed;right:16px;bottom:12px;font-size:11px;color:var(--faint)}
@media (max-width:900px){.app{grid-template-columns:1fr;grid-template-rows:auto auto 1fr}aside.rail{max-height:38vh}header.top{grid-column:1/2}}
@media print{
  .app{display:block;height:auto}aside.rail,.rail .tools,.flow .bar,.help{display:none !important}
  main{padding:0}.card{border:none;page-break-inside:avoid}.kids.collapsed{display:block}.row .code{display:none !important}
  .print-all .card{margin-bottom:28px}
}
@media (prefers-reduced-motion:no-preference){.ep,.row .body{transition:background .12s}}

/* ---- box diagram view ---- */
.dg{padding:8px 4px 16px;overflow-x:auto;display:flex;justify-content:flex-start}
.dg>.dg-col{margin:0 auto;min-width:max-content}
.dg-col{display:flex;flex-direction:column;align-items:center;min-width:180px}
.dg-node{position:relative;background:var(--surface);border:1.5px solid var(--line-strong);border-left-width:5px;border-radius:8px;padding:8px 12px 8px 12px;min-width:220px;max-width:420px;box-shadow:0 1px 2px rgba(27,36,48,.06);font-size:13.5px;line-height:1.35}
.dg-node .t{display:flex;align-items:baseline;gap:7px;font-weight:600}
.dg-node .t .g{font-family:var(--mono);font-size:11px;color:var(--muted);font-weight:700;flex:none}
.dg-node .f{font-family:var(--mono);font-size:11.5px;color:var(--muted);margin-top:4px;word-break:break-word;white-space:pre-wrap}
.dg-node .f.was{color:var(--removed);text-decoration:line-through}
.dg-node .f.now{color:var(--modified);font-weight:600;text-decoration:none}
.dg-node .f.meta{color:var(--faint);font-family:var(--sans);font-size:11.5px}
.dg-node .f .k{color:var(--faint)}
.dg-node .loc{display:block;text-align:right;font-family:var(--mono);font-size:10px;color:var(--faint);margin-top:4px}
.dg-node.k-call{border-left-color:#9AA5B1}.dg-node.k-io\.db{border-left-color:#0F6E9E}.dg-node.k-io\.http{border-left-color:#1E7F4F}.dg-node.k-io\.queue{border-left-color:#7C4DB3}.dg-node.k-io\.cache{border-left-color:#B25E09}.dg-node.k-io\.file{border-left-color:#5C6B7A}
.dg-node.k-validate{border-left-color:var(--ok)}.dg-node.k-auth{border-left-color:var(--patch)}.dg-node.k-transform,.dg-node.k-external{border-left-color:#C9D1DA;border-style:dashed}
.dg-node.k-branch,.dg-node.k-loop{border-color:var(--rail);border-left-width:1.5px;border-radius:14px;background:#F8F6FC}
.dg-node.k-try{border-color:var(--warn);border-left-width:1.5px;background:#FFFBF4}
.dg-node.k-return{border-radius:999px;border-color:var(--ink);border-left-width:1.5px;background:var(--ink);color:#fff;padding:8px 18px}
.dg-node.k-return .f,.dg-node.k-throw .f{color:rgba(255,255,255,.8)}.dg-node.k-return .t .g,.dg-node.k-throw .t .g{color:rgba(255,255,255,.75)}
.dg-node.k-throw{border-radius:999px;border-color:var(--bad);border-left-width:1.5px;background:var(--bad);color:#fff;padding:8px 18px}
.dg-node.k-return .loc,.dg-node.k-throw .loc{display:none}
.dg-node.c-added{background:var(--added-bg);border-color:var(--added)}.dg-node.c-removed{background:var(--removed-bg);border-color:var(--removed);opacity:.85}.dg-node.c-removed .t{text-decoration:line-through}
.dg-node.c-modified{background:var(--modified-bg);border-color:var(--modified)}
.dg-node.k-return.c-added,.dg-node.k-throw.c-added{background:var(--added);border-color:var(--added);color:#fff}
.dg-node.k-return.c-modified,.dg-node.k-throw.c-modified{background:var(--modified);border-color:var(--modified);color:#fff}
.dg-node.k-return.c-removed,.dg-node.k-throw.c-removed{background:var(--removed);border-color:var(--removed);color:#fff}
.dg-node .chg{position:absolute;left:-13px;top:-11px;width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-weight:800;font-size:13px;color:#fff;box-shadow:0 1px 2px rgba(0,0,0,.2)}
.dg-node .chg.added{background:var(--added)}.dg-node .chg.removed{background:var(--removed)}.dg-node .chg.modified{background:var(--modified)}.dg-node .chg.moved{background:var(--moved)}
.dg-link{width:2px;height:22px;background:var(--line-strong);position:relative;flex:none}
.dg-link:after{content:"";position:absolute;left:-4px;bottom:-1px;border:5px solid transparent;border-top:7px solid var(--line-strong);border-bottom:0}
.dg-io{border:1.5px solid var(--accent);background:var(--accent-soft);border-radius:8px;padding:8px 14px;min-width:260px;max-width:560px;font-size:13px}
.dg-io .t{font-family:var(--mono);font-weight:700;font-size:13.5px;display:flex;gap:8px;align-items:center}
.dg-io .f{font-family:var(--mono);font-size:11.5px;color:var(--muted);margin-top:3px}
.dg-io .f b{color:var(--ink);font-weight:600}
.dg-io .f.was{color:var(--removed);text-decoration:line-through}.dg-io .f.now{color:var(--modified);font-weight:600}
.dg-group{border:1.5px dashed var(--line-strong);border-radius:12px;padding:0 12px 12px;background:rgba(246,247,249,.6);display:flex;flex-direction:column;align-items:center;max-width:100%}
.dg-group>.dg-node{margin-top:-1px;border-top-left-radius:0;border-top-right-radius:0}
.dg-group>.dg-node.hd{margin:0 -12px 0;border-radius:12px 12px 0 0;width:calc(100% + 24px);max-width:none;border-left-width:1.5px;background:#EEF1F4}
.dg-group.c-added{border-color:var(--added)}.dg-group.c-removed{border-color:var(--removed)}
.dg-arms{display:flex;gap:26px;align-items:flex-start;justify-content:center;flex-wrap:nowrap;position:relative;padding-top:20px}
.dg-arm{display:flex;flex-direction:column;align-items:center;position:relative;min-width:200px;flex:0 0 auto}
.dg-arm:before{content:"";position:absolute;top:0;height:2px;background:var(--rail)}
.dg-arm.first:before{left:50%;right:-13px}.dg-arm.last:before{left:-13px;right:50%}.dg-arm.mid:before{left:-13px;right:-13px}.dg-arm.only:before{display:none}
.dg-arm:after{content:"";position:absolute;top:0;left:calc(50% - 1px);width:2px;height:20px;background:var(--rail)}
.dg-arm .lbl{position:relative;z-index:1;margin-top:20px;font-size:12px;font-weight:700;color:#4E4372;background:#EFEBF8;border:1px solid #D9CCEE;border-radius:999px;padding:2px 10px;max-width:100%;text-align:center}
.dg-arm .lbl.added{background:var(--added-bg);color:var(--added);border-color:#B7E1C8}.dg-arm .lbl.removed{background:var(--removed-bg);color:var(--removed);border-color:#F1B4AE;text-decoration:line-through}.dg-arm .lbl.modified{background:var(--modified-bg);color:var(--modified);border-color:#F3D3A6}
.dg-arm .lbl .was{display:block;font-weight:500;color:var(--removed);text-decoration:line-through;font-size:11px}
.dg-arm .none{font-size:11.5px;color:var(--faint);font-style:italic;margin-top:8px}
.dg-arm .exitmark{font-size:10.5px;color:var(--faint);font-family:var(--mono);margin-top:6px}
.dg-cstub{width:2px;height:16px;background:var(--rail);flex:none}
.dg-merge{width:2px;height:16px;background:var(--line-strong);flex:none;position:relative}
.dg-merge:before{content:"";position:absolute;left:-60px;right:-60px;top:0;height:2px;background:var(--line-strong)}
.dg-note{font-size:12px;color:var(--faint);font-style:italic}
.dg-legend{display:flex;flex-wrap:wrap;gap:12px 18px;font-size:12px;color:var(--muted);margin-top:14px;justify-content:center;align-items:center}
.dg-legend span{white-space:nowrap}
.dg-legend .note{white-space:normal;font-style:italic;flex-basis:100%;text-align:center}
.dg-legend span:not(.note):before{content:"";display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-2px;border:1.5px solid var(--line-strong)}
.dg-legend .lg-a:before{background:var(--added-bg);border-color:var(--added)}.dg-legend .lg-r:before{background:var(--removed-bg);border-color:var(--removed)}.dg-legend .lg-m:before{background:var(--modified-bg);border-color:var(--modified)}.dg-legend .lg-c:before{background:#F8F6FC;border-color:var(--rail)}.dg-legend .lg-e:before{background:var(--ink);border-color:var(--ink)}.dg-legend .lg-x:before{background:var(--bad);border-color:var(--bad)}
@media print{.dg{overflow:visible}}

/* ---- code changes (IDE-style diff) ---- */
.cd{margin-top:16px;border-top:1px solid var(--line);padding-top:14px}
.cd h3{margin:0 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;display:flex;align-items:center;gap:10px}
.cd h3 .sw{margin-left:auto;display:flex;gap:4px}
.cd h3 .sw button{font-size:11.5px;padding:2px 8px;text-transform:none;letter-spacing:0}
.cdf{border:1px solid var(--line-strong);border-radius:8px;margin-bottom:12px;overflow:hidden;background:var(--surface)}
.cdf>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:10px;padding:7px 12px;background:#F3F5F7;font-family:var(--mono);font-size:12.5px;border-bottom:1px solid var(--line)}
.cdf>summary::-webkit-details-marker{display:none}
.cdf>summary .tri{font-size:10px;color:var(--muted);width:10px}
.cdf[open]>summary .tri{transform:rotate(90deg)}
.cdf>summary .path{color:var(--ink);font-weight:600}
.cdf>summary .path .dir{color:var(--faint);font-weight:400}
.cdf>summary .stat{margin-left:auto;font-size:11.5px}
.cdf>summary .stat .p{color:var(--added);font-weight:700}.cdf>summary .stat .mnus{color:var(--removed);font-weight:700}
.cdf>summary .fst{font-size:10.5px;padding:1px 6px;border-radius:4px;border:1px solid var(--line-strong);color:var(--muted)}
.cdf>summary .fst.added{color:var(--added);border-color:#B7E1C8;background:var(--added-bg)}.cdf>summary .fst.deleted{color:var(--removed);border-color:#F1B4AE;background:var(--removed-bg)}
.ide{font-family:var(--mono);font-size:12px;line-height:1.55;overflow-x:auto;background:var(--surface)}
.ide table{border-collapse:collapse;width:100%;min-width:640px}
.ide td{padding:0 8px;white-space:pre;vertical-align:top}
.ide td.no{width:1%;min-width:38px;text-align:right;color:#9AA5B1;user-select:none;background:#F8F9FA;border-right:1px solid var(--line)}
.ide td.sg{width:1%;min-width:18px;text-align:center;color:var(--faint);user-select:none}
.ide tr.add td{background:#E6F4EA}.ide tr.add td.sg{color:var(--added);font-weight:700}.ide tr.add td.no{background:#D8ECDF;color:#4F7C5E}
.ide tr.del td{background:#FBE9E7}.ide tr.del td.sg{color:var(--removed);font-weight:700}.ide tr.del td.no{background:#F4D6D2;color:#8C3A33}
.ide tr.hunk td{background:#EEF3F8;color:#5C6B7A;font-size:11px;padding:3px 10px;border-top:1px solid #DCE6F0;border-bottom:1px solid #DCE6F0}
.ide tr.ctx td.code{color:#2E3A47}
.ide tr.hl td{outline:2px solid var(--accent);outline-offset:-2px}
.ide .w{background:rgba(30,127,79,.28);border-radius:2px}
.ide tr.del .w{background:rgba(180,35,24,.25)}
.ide td.empty{background:repeating-linear-gradient(45deg,#F6F7F9,#F6F7F9 6px,#EEF0F3 6px,#EEF0F3 12px)}
.ide.sbs td.code{width:50%;max-width:0;overflow:hidden;text-overflow:ellipsis}
.mini{margin-top:6px;border:1px solid var(--line-strong);border-radius:6px;overflow:hidden;font-family:var(--mono);font-size:11.5px;line-height:1.5;background:var(--surface);color:var(--ink);text-align:left;cursor:pointer}
.mini .ln{display:flex;white-space:pre}
.mini .ln .no{width:40px;flex:none;text-align:right;padding:0 6px;color:#9AA5B1;background:#F8F9FA;border-right:1px solid var(--line);user-select:none}
.mini .ln .sg{width:14px;flex:none;text-align:center;color:var(--faint)}
.mini .ln .code{padding:0 8px 0 2px;flex:1;overflow:hidden;text-overflow:ellipsis}
.mini .ln.add{background:#E6F4EA}.mini .ln.add .sg{color:var(--added);font-weight:700}
.mini .ln.del{background:#FBE9E7}.mini .ln.del .sg{color:var(--removed);font-weight:700}.mini .ln.del .code{color:#8C3A33}
.mini .ln .w{background:rgba(30,127,79,.28);border-radius:2px}.mini .ln.del .w{background:rgba(180,35,24,.25)}
.mini .cap{font-family:var(--sans);font-size:10.5px;color:var(--faint);padding:2px 8px;background:#F8F9FA;border-bottom:1px solid var(--line)}
.dg-node.k-return .mini,.dg-node.k-throw .mini{color:var(--ink)}
.cd .more{font-size:12px;color:var(--muted);margin:4px 0 0}

/* ---- side-by-side (before / after) diagrams ---- */
.sbs2{display:grid;grid-template-columns:1fr 1fr;gap:0;position:relative}
.sbs2 .side{padding:8px 10px 16px;min-width:0;overflow-x:auto}
.sbs2 .dg{padding:4px 0 12px}
.sbs2 .dg-node{max-width:340px;min-width:200px;font-size:12.5px}
.sbs2 .dg-node .f{font-size:11px}
.sbs2 .dg-arms{gap:16px}
.sbs2 .dg-arm{min-width:170px}
.sbs2 .dg-arm.first:before{right:-8px}.sbs2 .dg-arm.last:before{left:-8px}.sbs2 .dg-arm.mid:before{left:-8px;right:-8px}
.sbs2 .side.left{border-right:2px solid var(--line-strong);background:linear-gradient(90deg,transparent,rgba(180,35,24,.025))}
.sbs2 .side.right{background:linear-gradient(90deg,rgba(30,127,79,.03),transparent)}
.sbs2 .sh{position:sticky;top:0;z-index:2;display:flex;align-items:baseline;gap:8px;margin:0 0 12px;padding:6px 10px;border-radius:6px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;font-weight:700;background:var(--surface);border:1px solid var(--line)}
.sbs2 .sh .ref{font-family:var(--mono);text-transform:none;letter-spacing:0;font-weight:500;color:var(--muted);font-size:11.5px}
.sbs2 .side.left .sh{color:#8C3A33}.sbs2 .side.right .sh{color:var(--added)}
.dg-node.dim{opacity:.55}
.dg-node.side-l.c-modified{background:#FFF3E0;border-color:var(--modified)}
.dg-node .f .w{background:rgba(178,94,9,.28);border-radius:2px;text-decoration:none;color:var(--ink);font-weight:600}
.dg-node.side-l .f .w{background:rgba(180,35,24,.22)}
.dg-node.side-r .f .w{background:rgba(30,127,79,.25)}
.dg-node .t .w{background:rgba(178,94,9,.28);border-radius:2px}
.dg-ghost{border:1.5px dashed var(--line-strong);border-radius:8px;min-width:220px;max-width:420px;display:flex;align-items:center;justify-content:center;color:var(--faint);font-size:11.5px;font-style:italic;background:repeating-linear-gradient(135deg,transparent,transparent 8px,rgba(0,0,0,.025) 8px,rgba(0,0,0,.025) 16px);padding:8px 12px;text-align:center}
.dg-ghost.gr{border-color:var(--removed);color:var(--removed)}
.dg-ghost.ga{border-color:var(--added);color:var(--added)}
.dg-ghost.big{min-height:160px;width:100%;max-width:none;font-size:13px}
.dg-arm.ghost{opacity:.9}
.dg-arm.ghost .lbl{border-style:dashed;background:transparent;color:var(--faint)}
.pr-section{margin-bottom:28px}
.pr-section .card{max-width:none}
.pr-toc{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:10px 16px;margin-bottom:16px;font-size:13px}
.pr-toc a{cursor:pointer;color:var(--accent);text-decoration:none;margin-right:14px;white-space:nowrap;line-height:1.9}
.pr-toc a .m{margin-right:4px;font-size:9.5px;width:auto;padding:1px 6px}
@media (max-width:1100px){.sbs2{grid-template-columns:1fr}.sbs2 .side.left{border-right:none;border-bottom:2px solid var(--line-strong)}}
@media print{.sbs2{grid-template-columns:1fr 1fr}.sbs2 .side{overflow:visible}}
</style>
</head>
<body>
<div class="app" id="app"></div>
<div class="help"><kbd>j</kbd>/<kbd>k</kbd> endpoints · <kbd>/</kbd> search · <kbd>d</kbd> diagram/ledger · <kbd>x</kbd> code changes · <kbd>c</kbd> code · <kbd>e</kbd> expand all</div>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
'use strict';
const DATA = JSON.parse(document.getElementById('data').textContent);
const MODE = DATA.mode || 'scan';
const GLYPH = {call:'○','io.db':'db','io.http':'⇄','io.queue':'⇢','io.cache':'◫','io.file':'▤',branch:'◇',loop:'↻',try:'!',return:'◀',throw:'✕',validate:'✓',auth:'⚿',transform:'⇌',log:'≡',external:'◌',note:'…'};
const KIND_NAME = {call:'internal call','io.db':'database','io.http':'outbound HTTP','io.queue':'message queue','io.cache':'cache','io.file':'file / object storage',branch:'condition',loop:'loop',try:'error handling',return:'response',throw:'failure',validate:'validation',auth:'authorization',transform:'transform',log:'logging (hidden by default)',external:'external / unresolved',note:'note'};
const state = {sel:null, q:'', changedOnly: MODE==='diff', showCode:false, hideLog:true, expandAll:false, printAll:false, view: MODE==='diff'?'sidebyside':'ledger', focusChanges:true, codeChanges:false, sbs:false};
const FILE_DIFFS=(DATA.file_diffs&&DATA.file_diffs.files)||{};
const $ = (t,a,...kids)=>{const e=document.createElement(t);if(a)for(const k in a){if(k==='class')e.className=a[k];else if(k==='html')e.innerHTML=a[k];else if(k.startsWith('on'))e.addEventListener(k.slice(2),a[k]);else if(k==='text')e.textContent=a[k];else e.setAttribute(k,a[k]);}for(const c of kids.flat()){if(c==null||c===false)continue;e.append(c.nodeType?c:document.createTextNode(String(c)));}return e;};
const esc = s=>String(s==null?'':s);
function pathHtml(p){return esc(p).replace(/\{[^}]+\}/g,m=>'<span class="param">'+m+'</span>');}
function statusClass(c){return c>=500?'s5':c>=400?'s4':c>=300?'s3':'s2';}
function resourceOf(ep){const segs=ep.path.split('/').filter(Boolean).filter(s=>!/^(api|v\d+|internal|public|rest)$/i.test(s));const st=segs.filter(s=>!/^[{:]/.test(s));return st.length?'/'+st[0]:'/';}
function isChanged(ep){return ep.status && ep.status!=='unchanged';}
function visible(ep){if(MODE==='diff'&&state.changedOnly&&!isChanged(ep))return false;if(!state.q)return true;const q=state.q.toLowerCase();if((ep.id+' '+(ep.title||'')+' '+(ep.handler&&ep.handler.name||'')).toLowerCase().includes(q))return true;return JSON.stringify(ep.flow||[]).toLowerCase().includes(q);}
function countChanged(){return DATA.endpoints.filter(isChanged).length;}

function render(){
  const app=document.getElementById('app');app.innerHTML='';
  app.append(renderHeader(), renderRail(), renderMain());
}
function renderHeader(){
  const repo=(DATA.repo&&DATA.repo.name)||'API';
  const h=$('header',{class:'top'});
  h.append($('div',{}, $('h1',{text: repo+(MODE==='diff'?' — API change review':' — API flow map')}), $('div',{class:'sub',text:(DATA.frameworks||[]).join(', ')+(DATA.generated_at?' · generated '+DATA.generated_at.replace('T',' ').slice(0,16):'')})));
  if(MODE==='diff'){
    const b=DATA.base||{},hd=DATA.head||{};
    h.append($('div',{class:'refs'}, $('span',{text:'base'}), $('b',{text:(b.ref||b.branch||'base')+(b.commit?' @'+b.commit:'')}), $('span',{text:'→'}), $('span',{text:'head'}), $('b',{text:(hd.branch||hd.ref||'working tree')+(hd.commit?' @'+hd.commit:'')+(hd.dirty?' (uncommitted changes included)':'')})));
    const s=DATA.summary||{};
    h.append($('div',{class:'spacer'}));
    h.append($('span',{class:'pill added',text:'+'+(s.endpoints_added||0)+' endpoints'}),$('span',{class:'pill removed',text:'−'+(s.endpoints_removed||0)}),$('span',{class:'pill modified',text:'~'+(s.endpoints_modified||0)}),$('span',{class:'pill',text:(s.endpoints_unchanged||0)+' unchanged'}));
    h.append($('span',{class:'pill risk-'+(s.risk||'none'),text:'risk: '+(s.risk||'none')}));
  } else {
    const st=DATA.stats||{};h.append($('div',{class:'spacer'}));
    h.append($('span',{class:'pill',text:(st.endpoints||DATA.endpoints.length)+' endpoints'}),$('span',{class:'pill',text:(st.steps||0)+' steps'}),$('span',{class:'pill',text:(st.files_scanned||0)+' files'}));
    if(st.untraced_endpoints)h.append($('span',{class:'pill modified',text:st.untraced_endpoints+' untraced'}));
    const g=(DATA.repo&&DATA.repo.git)||{};if(g.branch)h.append($('span',{class:'refs'},$('b',{text:g.branch+(g.commit?' @'+g.commit:'')+(g.dirty?' (dirty)':'')})));
  }
  return h;
}
function renderRail(){
  const rail=$('aside',{class:'rail'});
  const tools=$('div',{class:'tools'});
  const inp=$('input',{type:'search',placeholder:'Search endpoints, steps, handlers…',value:state.q,oninput:e=>{state.q=e.target.value;renderRailList(list);}});
  inp.id='q';
  const tg=$('div',{class:'toggles'});
  if(MODE==='diff')tg.append($('button',{class:state.changedOnly?'on':'',text:'Changed only ('+countChanged()+')',onclick:()=>{state.changedOnly=!state.changedOnly;render();}}));
  tg.append($('button',{class:state.showCode?'on':'',text:'Code',onclick:()=>{state.showCode=!state.showCode;render();}}));
  tg.append($('button',{class:state.hideLog?'':'on',text:'Logs',onclick:()=>{state.hideLog=!state.hideLog;render();}}));
  tg.append($('button',{class:state.expandAll?'on':'',text:'Expand all',onclick:()=>{state.expandAll=!state.expandAll;render();}}));
  tg.append($('button',{class:state.printAll?'on':'',text:MODE==='diff'?'Whole PR':'All endpoints',title:MODE==='diff'?'Every changed endpoint on one page, before/after side by side':'Show every endpoint in the main pane (for printing / sharing)',onclick:()=>{state.printAll=!state.printAll;render();}}));
  tools.append(inp,tg);
  const list=$('div',{});
  rail.append(tools,list);renderRailList(list);
  return rail;
}
function renderRailList(list){
  list.innerHTML='';
  const eps=DATA.endpoints.filter(visible);
  const groups={};for(const ep of eps){const k=resourceOf(ep);(groups[k]=groups[k]||[]).push(ep);}
  const keys=Object.keys(groups).sort();
  if(!keys.length){list.append($('div',{class:'empty-state',text:MODE==='diff'&&state.changedOnly?'No endpoint changed between base and head.':'No endpoints match.'}));return;}
  for(const k of keys){
    const g=$('div',{class:'group'},$('h3',{text:k}));
    for(const ep of groups[k]){
      const el=$('div',{class:'ep st-'+(ep.status||'unchanged')+(state.sel===ep.id?' sel':''),onclick:()=>{state.sel=ep.id;state.printAll=false;render();}});
      el.dataset.id=ep.id;
      const badges=$('div',{class:'badges'});
      if(MODE==='diff'&&isChanged(ep))badges.append($('span',{class:'chg '+ep.status,text:ep.status==='added'?'+ new':ep.status==='removed'?'− removed':'~ '+((ep.counts.added||0)+(ep.counts.removed||0)+(ep.counts.modified||0)+ep.property_changes.length)+' changes'}));
      if(ep.risk&&ep.risk!=='none')badges.append($('span',{class:'dot '+ep.risk,title:'risk: '+ep.risk}));
      if(ep.source==='openapi')badges.append($('span',{class:'tag',text:'spec only',style:'font-size:10px;color:var(--warn)'}));
      el.append($('span',{class:'m '+ep.method,text:ep.method}), $('div',{}, $('div',{class:'path',html:pathHtml(ep.path)}), $('div',{class:'tline'},$('div',{class:'t',text:ep.title||ep.auto_title||''}),badges)));
      g.append(el);
    }
    list.append(g);
  }
}
function renderMain(){
  const main=$('main',{});
  if(MODE==='diff'||(typeof DATA.digest==='string'&&DATA.digest.trim()))main.append(renderDigest());
  const eps=DATA.endpoints.filter(visible);
  if(state.printAll&&MODE==='diff'){
    main.classList.add('print-all');
    const list=eps.filter(isChanged);
    const toc=$('div',{class:'pr-toc'},$('b',{text:list.length+' changed endpoint'+(list.length===1?'':'s')+': '}));
    for(const ep of list)toc.append($('a',{onclick:()=>{const el=document.getElementById('ep-'+ep.id.replace(/[^\w]+/g,'_'));if(el)el.scrollIntoView({behavior:'smooth',block:'start'});}},$('span',{class:'m '+ep.method,text:ep.method}),ep.path));
    main.append(toc);
    if(state.view!=='sidebyside'&&state.view!=='diagram')state.view='sidebyside';
    for(const ep of list){const sec=$('div',{class:'pr-section'});sec.append(renderEndpoint(ep));main.append(sec);}
    if(!list.length)main.append($('div',{class:'empty-state',text:'No endpoint changed between base and head.'}));
    scheduleEqualize(main);
    return main;
  }
  if(state.printAll){main.classList.add('print-all');for(const ep of eps)main.append(renderEndpoint(ep));return main;}
  let ep=DATA.endpoints.find(e=>e.id===state.sel);
  if(!ep||!visible(ep)){ep=eps[0];state.sel=ep?ep.id:null;}
  if(!ep){main.append($('div',{class:'empty-state',text:'Nothing to show.'}));return main;}
  main.append(renderEndpoint(ep));
  scheduleEqualize(main);
  const diags=MODE==='diff'?[...((DATA.diagnostics||{}).head||[]),...((DATA.diagnostics||{}).base||[]).map(d=>'(base) '+d)]:(DATA.diagnostics||[]);
  if(diags.length){main.append($('details',{class:'diag'},$('summary',{text:diags.length+' scan diagnostics'}),$('ul',{},diags.map(d=>$('li',{text:d})))));}
  return main;
}
function renderDigest(){
  const d=$('section',{class:'digest'});
  d.append($('h2',{text:MODE==='diff'?'What changed':'Overview'}));
  if(DATA.digest)d.append($('div',{class:'text',html:mdLite(DATA.digest)}));
  if(MODE==='diff'){
    const s=DATA.summary||{};const rs=s.risk_reasons||[];
    if(!DATA.digest)d.append($('div',{class:'text',text:(s.endpoints_added||0)+' endpoint(s) added, '+(s.endpoints_removed||0)+' removed, '+(s.endpoints_modified||0)+' modified; '+(s.steps_added||0)+' flow steps added, '+(s.steps_removed||0)+' removed, '+(s.steps_modified||0)+' modified.'}));
    if(rs.length){const ul=$('ul',{});for(const r of rs){ul.append($('li',{},$('span',{class:'sev '+r.severity,text:r.severity.toUpperCase()+' '}),' ',$('span',{class:'ep-link',text:r.endpoint,onclick:()=>{state.sel=r.endpoint;state.printAll=false;if(state.changedOnly&&!isChanged(DATA.endpoints.find(e=>e.id===r.endpoint)||{}))state.changedOnly=false;render();}}),' — ',r.reason));}d.append(ul);}
  }
  return d;
}
function mdLite(t){return esc(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>').replace(/`([^`]+)`/g,'<code>$1</code>');}
function propChips(ep){
  const p=ep.properties||{};const changes={};for(const c of (ep.property_changes||[]))changes[c.key]=c;
  const wrap=$('div',{class:'props'});
  const order=['route','auth','spec_auth','validation','body','path_params','query_params','headers','status','responses','declared_responses','returns','documented','deprecated','feature_flags','rate_limit','cache','transactional','timeout','consumes','produces','async','reactive','resilience','version','lambda'];
  const seen=new Set();
  const fmt=v=>Array.isArray(v)?v.join(', '):(typeof v==='boolean'?(v?'yes':'no'):String(v));
  const label={auth:'auth',spec_auth:'spec auth',validation:'validation',body:'body',path_params:'path',query_params:'query',headers:'headers',status:'status',responses:'responds',declared_responses:'documented codes',returns:'returns',documented:'documented',deprecated:'deprecated',feature_flags:'flags',rate_limit:'rate limit',cache:'cache',transactional:'transactional',timeout:'timeout',consumes:'consumes',produces:'produces',async:'async',reactive:'reactive',resilience:'resilience',route:'route',version:'version',lambda:'lambda'};
  for(const k of order){
    const c=changes[k];const cur=p[k];seen.add(k);
    if(c){const chip=$('span',{class:'prop '+(c.before==null?'added':c.after==null?'removed':'modified'),title:c.reason},$('b',{text:label[k]||k}));if(c.before!=null)chip.append($('span',{class:'was',text:fmt(c.before)}));if(c.after!=null)chip.append($('span',{text:fmt(c.after)}));wrap.append(chip);continue;}
    if(cur==null||cur===false||(Array.isArray(cur)&&!cur.length))continue;
    if(k==='documented'&&cur===true){wrap.append($('span',{class:'prop'},$('b',{text:'documented'}),'yes'));continue;}
    if(k==='responses'){const chip=$('span',{class:'prop'},$('b',{text:'responds'}));for(const code of cur)chip.append($('span',{class:'st '+statusClass(code),text:code}));wrap.append(chip);continue;}
    wrap.append($('span',{class:'prop'},$('b',{text:label[k]||k}),$('span',{text:fmt(cur)})));
  }
  for(const k in changes)if(!seen.has(k)){const c=changes[k];wrap.append($('span',{class:'prop modified',title:c.reason},$('b',{text:k}),c.before!=null?$('span',{class:'was',text:fmt(c.before)}):null,c.after!=null?$('span',{text:fmt(c.after)}):null));}
  if(p.documented===false)wrap.append($('span',{class:'prop',style:'color:var(--warn)'},$('b',{text:'documented'}),'no'));
  return wrap;
}
function renderEndpoint(ep){
  const card=$('section',{class:'card'});card.id='ep-'+ep.id.replace(/[^\w]+/g,'_');
  const head=$('div',{class:'head'});
  const route=$('div',{class:'route'},$('span',{class:'m '+ep.method,text:ep.method}),$('span',{class:'p',html:pathHtml(ep.path)}));
  if(MODE==='diff'&&isChanged(ep))route.append($('span',{class:'chg '+ep.status,text:ep.status}));
  if(ep.before&&ep.before.id&&ep.before.id!==ep.id)route.append($('span',{class:'p was',text:ep.before.id}));
  if(ep.risk&&ep.risk!=='none')route.append($('span',{class:'pill risk-'+ep.risk,text:'risk '+ep.risk}));
  if(ep.note)route.append($('span',{class:'pill',text:ep.note}));
  if(ep.source==='openapi')route.append($('span',{class:'pill modified',text:'declared in spec, no implementation found'}));
  head.append(route);
  const h2=$('h2',{text:ep.title||ep.auto_title||''});if(ep.title&&ep.auto_title&&ep.title!==ep.auto_title)h2.append($('span',{class:'auto',text:'auto: '+ep.auto_title}));
  head.append(h2);
  if(ep.summary)head.append($('div',{class:'summary',text:ep.summary}));
  if(ep.handler&&ep.handler.file)head.append($('div',{class:'handler',text:(ep.handler.name?ep.handler.name+' · ':'')+ep.handler.file+(ep.handler.line?':'+ep.handler.line:'')+(ep.framework?' · '+ep.framework:'')}));
  head.append(propChips(ep));
  for(const w of (ep.warnings||[]))head.append($('div',{class:'warn',text:'⚠ '+w}));
  card.append(head);
  if(MODE==='diff'&&(ep.changes_summary||(ep.risk_reasons||[]).length||(ep.flow_changes||[]).length)){
    const ch=$('div',{class:'changes'});ch.append($('h3',{text:'Changes in this endpoint'}));
    if(ep.changes_summary)ch.append($('p',{class:'csum',html:mdLite(ep.changes_summary)}));
    const ol=$('ol',{});
    for(const r of (ep.risk_reasons||[]).filter(r=>!(ep.flow_changes||[]).some(c=>c.reason===r.reason)))ol.append($('li',{},$('span',{class:'sev '+r.severity,text:r.severity}),$('span',{class:'reason',text:r.reason})));
    for(const c of (ep.flow_changes||[])){ol.append($('li',{onclick:()=>jumpTo(c.step_id)},$('span',{class:'sev '+c.severity,text:c.severity}),$('span',{},$('span',{class:'reason',text:c.reason}),c.path&&c.path.length?$('div',{class:'crumb',text:'in: '+c.path.join(' › ')}):null)));}
    if(ol.children.length)ch.append(ol);card.append(ch);
  }
  const flow=$('div',{class:'flow'+(state.showCode?' showcode':'')+(state.hideLog?' hidekind-log':'')});
  const bar=$('div',{class:'bar'});
  bar.append($('span',{text:'View:'}),
    $('button',{class:state.view==='ledger'?'on':'',text:'Ledger',onclick:()=>{state.view='ledger';render();}}),
    $('button',{class:state.view==='diagram'?'on':'',text:MODE==='diff'?'Merged diagram':'Box diagram',onclick:()=>{state.view='diagram';render();}}));
  if(MODE==='diff')bar.append($('button',{class:state.view==='sidebyside'?'on':'',text:'Before / after diagrams',title:'Base branch on the left, this branch on the right, aligned step by step',onclick:()=>{state.view='sidebyside';render();}}));
  if((state.view==='diagram'||state.view==='sidebyside')&&MODE==='diff')bar.append($('button',{class:state.focusChanges?'on':'',text:'Focus on changes',title:'Collapse internal calls that did not change',onclick:()=>{state.focusChanges=!state.focusChanges;render();}}));
  if(MODE==='diff'&&Object.keys(FILE_DIFFS).length)bar.append($('span',{style:'margin-left:12px'}),$('button',{class:state.codeChanges?'on':'',text:'⟨/⟩ Code changes',title:'Show the source diff next to each changed step and the full file diffs (like an IDE diff editor)',onclick:()=>{state.codeChanges=!state.codeChanges;render();}}));
  flow.append(bar);
  if(!(ep.flow||[]).length){flow.append($('div',{class:'empty-state',text:ep.source==='openapi'?'No implementation to trace.':'No traceable steps in this handler.'}));}
  else if(state.view==='sidebyside'&&MODE==='diff'){flow.append(renderSideBySide(ep));}
  else if(state.view==='diagram'){flow.append(renderDiagram(ep));}
  else{const led=$('div',{class:'ledger'});renderSteps(ep.flow,led,0);flow.append(led);}
  if(MODE==='diff'&&state.codeChanges)flow.append(renderCodePanel(ep));
  if(state.view==='ledger'){const leg=$('div',{class:'legend'});for(const k of ['call','io.db','io.http','io.queue','io.cache','io.file','branch','loop','try','return','throw','validate','auth','transform','external']){leg.append($('span',{},$('span',{class:'glyph g-'+k},$('span',{text:GLYPH[k]})),KIND_NAME[k]));}
  if(MODE==='diff')leg.append($('span',{},$('span',{class:'chg added',text:'+'}),' added ',$('span',{class:'chg removed',text:'−'}),' removed ',$('span',{class:'chg modified',text:'~'}),' modified'));
  flow.append(leg);}
  card.append(flow);
  return card;
}
function jumpTo(id){const el=document.querySelector('[data-step="'+CSS.escape(id)+'"]');if(!el)return;let p=el.parentElement;while(p){if(p.classList&&p.classList.contains('kids'))p.classList.remove('collapsed');p=p.parentElement;}el.scrollIntoView({block:'center',behavior:'smooth'});el.querySelector('.body').animate([{outline:'2px solid var(--accent)'},{outline:'2px solid transparent'}],{duration:1600});}
function renderSteps(nodes,container,depth){
  const vis=nodes.filter(n=>n.change!=='moved-from');
  vis.forEach((n,i)=>container.append(renderStep(n,depth,i===0,i===vis.length-1)));
}
function renderStep(n,depth,first,last){
  const change=n.change||'same';
  const row=$('div',{class:'row kind-'+n.kind+' '+change+(first?' first':'')+(last?' last':'')});row.dataset.step=n.id||'';
  row.append($('div',{class:'gut',text:change==='added'?'+':change==='removed'?'−':change==='modified'?'~':change==='moved'?'⇅':''}));
  row.append($('div',{class:'spine'},$('span',{class:'glyph g-'+n.kind},$('span',{text:GLYPH[n.kind]||'·'}))));
  const body=$('div',{class:'body'});
  const lbl=$('div',{class:'lbl'});
  const txt=$('span',{class:'txt'+((n.kind==='branch'||n.kind==='return'||n.kind==='throw')?' bold':''),text:n.label||''});
  lbl.append(txt);
  const meta=$('span',{class:'meta'});
  if(n.outcome&&n.outcome.status)meta.append($('span',{class:'st '+statusClass(n.outcome.status),text:n.outcome.status}));
  if(n.outcome&&n.outcome.throws&&n.kind!=='throw')meta.append($('span',{class:'thr',text:'throws '+n.outcome.throws}));
  for(const t of (n.tags||[])){if(t==='wrapper'||t==='guard'||t==='callback'||t==='error-propagation')continue;if(t.startsWith('flag:')){meta.append($('span',{class:'tag flag',text:'flag '+t.slice(5)}));continue;}if(t==='feature-flag')continue;meta.append($('span',{class:'tag'+(t==='early-exit'?' exit':''),text:t}));}
  if(n.nested_changes&&MODE==='diff'){const nc=n.nested_changes;const parts=[];if(nc.added)parts.push('+'+nc.added);if(nc.removed)parts.push('−'+nc.removed);if(nc.modified)parts.push('~'+nc.modified);if(parts.length)meta.append($('span',{class:'tag',text:parts.join(' ')+' inside',style:'color:var(--modified);border-color:#F3D3A6;background:var(--modified-bg)'}));}
  lbl.append(meta);
  const hasKids=(n.children||[]).length>0;
  let kids=null;
  if(hasKids){const collapsed=!state.expandAll&&(depth>=1||(n.tags||[]).includes('wrapper'))&&!(n.nested_changes&&MODE==='diff');kids=$('div',{class:'kids'+(collapsed?' collapsed':'')});const ex=$('span',{class:'exp',text:collapsed?'▸ '+countAll(n.children)+' steps':'▾',onclick:()=>{kids.classList.toggle('collapsed');ex.textContent=kids.classList.contains('collapsed')?'▸ '+countAll(n.children)+' steps':'▾';}});lbl.append(ex);}
  if(n.location&&n.location.file)lbl.append($('span',{class:'loc',text:n.location.file.split('/').slice(-2).join('/')+(n.location.line?':'+n.location.line:'')}));
  body.append(lbl);
  if(change==='modified'&&n.before){const b=n.before;const diffs=[];if((n.changed_fields||[]).includes('condition'))diffs.push(b.label||b.condition);else if((n.changed_fields||[]).includes('outcome'))diffs.push(b.label+(b.outcome&&b.outcome.status?' ['+b.outcome.status+']':''));else if(b.label!==n.label)diffs.push(b.label);else diffs.push(b.detail);body.append($('div',{class:'was'},$('b',{text:'was'}),diffs.join(' · ')));}
  if(n.summary)body.append($('div',{class:'sumline',text:n.summary}));
  if(state.codeChanges&&change!=='same'){const m=miniDiff(n);if(m)body.append(m);}
  const code=n.code||n.detail;if(code)body.append($('div',{class:'code',text:code}));
  row.append(body);
  if(kids){renderSteps(n.children,kids,depth+1);row.append(kids);}
  if((n.branches||[]).length){
    const br=$('div',{class:'branch'});
    n.branches.forEach((arm,ai)=>{
      const steps=(arm.steps||[]).filter(s=>s.change!=='moved-from');
      // an `if` step already carries its first arm's condition as its label: fold that arm into the step
      const folded=(ai===0&&n.kind==='branch'&&n.detail&&n.detail.startsWith('if (')&&((arm.change||'same')===change||(arm.change||'same')==='same'||(arm.change==='modified'&&change==='modified')));
      if(folded){if(arm.exits)txt.after($('span',{class:'tag exit',text:'exits',style:'font-size:10.5px;padding:1px 6px;border-radius:4px;background:var(--bg);border:1px solid var(--line);color:var(--muted);font-family:var(--mono)'}));}
      else{
        const a=$('div',{class:'arm '+(arm.change||'same')});
        a.append($('span',{class:'diamond',text:'◇'}),$('span',{class:'alabel',text:arm.label}));
        if(arm.change==='modified'&&arm.before&&arm.before.label!==arm.label)a.append($('span',{class:'awas',text:arm.before.label}));
        if(arm.exits)a.append($('span',{class:'exit',text:'exits'}));
        br.append(a);
      }
      if(steps.length){const as=$('div',{class:'armsteps'});renderSteps(arm.steps,as,depth+1);br.append(as);}
      else br.append($('div',{class:'empty',text:'no traced steps'}));
    });
    row.append(br);
  }
  return row;
}

// ---------------------------------------------------------------- box diagram
function argsOf(text){const i=text.indexOf('(');if(i<0)return null;let depth=0;for(let j=i;j<text.length;j++){const ch=text[j];if(ch==='('||ch==='['||ch==='{')depth++;else if(ch===')'||ch===']'||ch==='}'){depth--;if(depth===0)return text.slice(i+1,j);}}return text.slice(i+1);}
function calleeOf(text){const i=text.indexOf('(');const head=(i<0?text:text.slice(0,i)).trim();const parts=head.split('.');return parts.slice(-2).join('.');}
function clip(t,n){t=String(t||'').replace(/\s+/g,' ').trim();return t.length>n?t.slice(0,n-1)+'…':t;}
function hasChangeIn(n){return n.change&&n.change!=='same'||(n.nested_changes&&(n.nested_changes.added||n.nested_changes.removed||n.nested_changes.modified));}
function callFacts(detail){const out=[];for(const part of String(detail||'').split(' → ')){const a=argsOf(part);if(a!==null&&a.trim())out.push(calleeOf(part)+'('+clip(a,70)+')');else if(part.trim()&&!/^(try\/catch|switch|if)/.test(part))out.push(clip(part,80));}return out.slice(0,2);}
function factsFor(n){
  const out=[];const d=n.detail||'';
  if(n.kind==='branch'){if(n.condition)out.push({c:'',t:clip(n.condition,90)});}
  else if(n.kind==='loop'){if(n.condition)out.push({c:'',t:clip(n.condition,90)});}
  else if(n.kind==='return'){const m=/^return\s+(.*)$/s.exec(d);const body=m?m[1]:d;if(body&&!/^(ResponseEntity\.(ok|noContent|notFound|badRequest)\(\)\.build\(\)|res\.sendStatus\(\d+\))$/.test(body.trim()))out.push({c:'',t:clip(body,90)});}
  else if(n.kind==='throw'){const body=d.replace(/^(throw|raise)\s+/,'').trim();if(body&&!/^[a-z_]\w{0,3}$/.test(body))out.push({c:'',t:clip(body,90)});}
  else if(n.kind==='try'){}
  else{for(const f of callFacts(d))out.push({c:'',t:f});}
  if(n.change==='modified'&&n.before){const b=n.before;let was='';
    if((n.changed_fields||[]).includes('condition'))was=b.condition||b.label;
    else if((n.changed_fields||[]).includes('outcome'))was=(b.label||'')+(b.outcome&&b.outcome.status?'  ['+b.outcome.status+']':'');
    else if((n.changed_fields||[]).includes('target'))was=b.target||b.detail;
    else was=b.detail||b.label;
    if(was){out.push({c:'was',t:'was  '+clip(was,90)});}
  }
  if(n.outcome&&n.outcome.throws&&n.kind!=='throw')out.push({c:'meta',t:'throws '+n.outcome.throws});
  for(const t of (n.tags||[]))if(t.startsWith('flag:'))out.push({c:'meta',t:'feature flag: '+t.slice(5)});
  if(n.tags&&n.tags.includes('recursion'))out.push({c:'meta',t:'recursive call, not expanded'});
  if(n.tags&&n.tags.includes('depth-limit'))out.push({c:'meta',t:'not expanded (depth limit)'});
  return out;
}
function dgNode(n,extraClass){
  const change=n.change||'same';
  const el=$('div',{class:'dg-node k-'+n.kind+' c-'+change+(extraClass?' '+extraClass:'')});
  el.dataset.step=n.id||'';
  if(change!=='same'&&change!=='moved-from')el.append($('span',{class:'chg '+change,text:change==='added'?'+':change==='removed'?'−':change==='modified'?'~':'⇅'}));
  const t=$('div',{class:'t'},$('span',{class:'g',text:GLYPH[n.kind]||'·'}),$('span',{text:n.label||''}));
  if(n.outcome&&n.outcome.status)t.append($('span',{class:'st '+statusClass(n.outcome.status),text:n.outcome.status,style:'margin-left:auto'}));
  el.append(t);
  for(const f of factsFor(n))el.append($('div',{class:'f '+f.c,text:f.t}));
  if(state.codeChanges&&change!=='same'){const m=miniDiff(n);if(m){el.append(m);el.style.borderRadius='12px';}}
  if(n.location&&n.location.file&&n.kind!=='return'&&n.kind!=='throw')el.append($('div',{class:'loc',text:n.location.file.split('/').pop()+(n.location.line?':'+n.location.line:'')}));
  return el;
}
function dgLink(col){col.append($('div',{class:'dg-link'}));}
function dgColumn(nodes,depth,changeCtx){
  const col=$('div',{class:'dg-col'});
  const vis=nodes.filter(n=>n.change!=='moved-from'&&!(state.hideLog&&n.kind==='log'));
  vis.forEach((n,i)=>{
    if(i>0)dgLink(col);
    dgAppend(n,col,depth,changeCtx);
  });
  return col;
}
function dgAppend(n,col,depth,changeCtx){
  const kids=(n.children||[]).filter(k=>!(state.hideLog&&k.kind==='log'));
  const isWrapper=(n.tags||[]).includes('wrapper');
  const expand=kids.length&&!isWrapper&&(state.expandAll||(MODE==='diff'&&state.focusChanges?(hasChangeIn(n)&&n.change==='same')||(n.change!=='same'&&depth<1):depth<1));
  if(kids.length&&expand){
    const g=$('div',{class:'dg-group c-'+(n.change||'same')});
    g.append(dgNode(n,'hd'));
    dgLink(g);
    g.append(dgColumn(kids,depth+1,changeCtx||n.change));
    col.append(g);
  } else {
    const node=dgNode(n,'');
    if(kids.length&&!isWrapper)node.append($('div',{class:'f meta',text:countAll(kids)+' steps inside'+(n.nested_changes&&MODE==='diff'?' · '+[n.nested_changes.added?'+'+n.nested_changes.added:'',n.nested_changes.removed?'−'+n.nested_changes.removed:'',n.nested_changes.modified?'~'+n.nested_changes.modified:''].filter(Boolean).join(' ')+' changed':'')}));
    col.append(node);
  }
  if((n.branches||[]).length){
    col.append($('div',{class:'dg-cstub'}));
    const arms=$('div',{class:'dg-arms'});
    const isIf=(n.kind==='branch'&&String(n.detail||'').startsWith('if ('));
    const hasElse=(n.branches||[]).some(b=>b.label==='Otherwise');
    const list=(isIf&&!hasElse)?n.branches.concat([{label:'no',implicit:true,steps:[],exits:false,change:n.change==='removed'?'removed':'same'}]):n.branches;
    let anyContinue=false;
    list.forEach((arm,ai)=>{
      const pos=list.length===1?'only':ai===0?'first':ai===list.length-1?'last':'mid';
      const a=$('div',{class:'dg-arm '+pos});
      const firstIf=(ai===0&&n.kind==='branch'&&String(n.detail||'').startsWith('if ('));
      const lbl=$('div',{class:'lbl '+(arm.change||'same'),text:firstIf?'yes':arm.label});
      if(arm.implicit)lbl.style.opacity='.75';
      if(arm.change==='modified'&&arm.before&&arm.before.label!==arm.label&&!firstIf)lbl.append($('span',{class:'was',text:'was: '+arm.before.label}));
      a.append(lbl);
      const steps=(arm.steps||[]).filter(s=>s.change!=='moved-from');
      if(arm.implicit){a.append($('div',{class:'none',text:'continue ↓'}));}
      else if(steps.length){a.append($('div',{class:'dg-cstub'}));a.append(dgColumn(steps,depth+1,changeCtx));}
      else a.append($('div',{class:'none',text:'nothing else happens'}));
      if(arm.exits)a.append($('div',{class:'exitmark',text:'▣ ends here'}));else anyContinue=true;
      arms.append(a);
    });
    col.append(arms);
    if(anyContinue)col.append($('div',{class:'dg-merge'}));
  }
}
function dgInputs(ep){
  const p=ep.properties||{};const changes={};for(const c of (ep.property_changes||[]))changes[c.key]=c;
  const box=$('div',{class:'dg-io'});
  box.append($('div',{class:'t'},$('span',{class:'m '+ep.method,text:ep.method}),$('span',{html:pathHtml(ep.path)})));
  const fmt=v=>Array.isArray(v)?v.join(', '):(typeof v==='boolean'?(v?'yes':'no'):String(v));
  const rows=[['auth','auth'],['validation','validation'],['body','body'],['path_params','path'],['query_params','query'],['headers','headers'],['form','form'],['status','declared status'],['feature_flags','feature flags'],['returns','returns']];
  for(const [k,label] of rows){
    const c=changes[k];
    if(c){box.append($('div',{class:'f'},$('b',{text:label+': '}),c.before!=null?$('span',{class:'was',text:fmt(c.before)}):null,' ',$('span',{class:'now',text:c.after!=null?fmt(c.after):'(removed)'})));continue;}
    const v=p[k];if(v==null||v===false||(Array.isArray(v)&&!v.length))continue;
    box.append($('div',{class:'f'},$('b',{text:label+': '}),fmt(v)));
  }
  if(!box.querySelector('.f'))box.append($('div',{class:'f',text:'no inputs detected'}));
  return box;
}
function renderDiagram(ep){
  const wrap=$('div',{class:'dg'});
  const col=$('div',{class:'dg-col'});
  col.append(dgInputs(ep));
  dgLink(col);
  col.append(dgColumn(ep.flow||[],0,ep.status));
  wrap.append(col);
  const leg=$('div',{class:'dg-legend'});
  leg.append($('span',{class:'lg-c',text:'condition'}),$('span',{class:'lg-e',text:'response'}),$('span',{class:'lg-x',text:'failure / exception'}));
  if(MODE==='diff')leg.append($('span',{class:'lg-a',text:'added'}),$('span',{class:'lg-r',text:'removed'}),$('span',{class:'lg-m',text:'modified'}));
  leg.append($('span',{class:'note',text:'each box lists the variables and arguments involved; changed boxes show the old value struck through; file:line at the bottom right'}));
  wrap.append(leg);
  return wrap;
}

// ---------------------------------------------------------------- code changes (IDE-style diff)
function hlLine(text,ranges){const frag=document.createDocumentFragment();let pos=0;for(const [a,b] of (ranges||[])){if(a>pos)frag.append(document.createTextNode(text.slice(pos,a)));frag.append($('span',{class:'w',text:text.slice(a,b)}));pos=b;}if(pos<text.length)frag.append(document.createTextNode(text.slice(pos)));if(!text.length)frag.append(document.createTextNode(' '));return frag;}
function hunkFor(fd,line,useOld){for(const h of fd.hunks){const nums=h.lines.map(l=>useOld?l.o:l.n).filter(x=>x!=null);if(!nums.length)continue;if(line>=Math.min(...nums)-1&&line<=Math.max(...nums)+1)return h;}return null;}
function miniDiff(n){
  const removed=n.change==='removed';
  const loc=removed?(n.location||{}):(n.location||{});
  const fd=FILE_DIFFS[loc.file];if(!fd||!loc.line)return null;
  const h=hunkFor(fd,loc.line,removed);if(!h)return null;
  const key=removed?'o':'n';const L=h.lines;
  let ti=L.findIndex(l=>l[key]===loc.line);
  if(ti<0)return null;
  let rows=[];
  if(L[ti].t==='+'||L[ti].t==='-'){
    // the contiguous run of changed lines around the step, plus its counterpart run (the "was" side)
    let a=ti,b=ti;while(a>0&&L[a-1].t===L[ti].t)a--;while(b<L.length-1&&L[b+1].t===L[ti].t)b++;
    if(L[ti].t==='+'){let c=a-1;while(c>=0&&L[c].t==='-')c--;rows=L.slice(c+1,b+1);}
    else{let c=b+1;while(c<L.length&&L[c].t==='+')c++;rows=L.slice(a,c);}
    // keep it focused: at most 3 lines either side of the step line
    rows=rows.filter(l=>l[key]==null?true:Math.abs(l[key]-loc.line)<=3);
    if(rows.length>8)rows=rows.slice(0,8);
  } else {
    // step line itself unchanged: show the nearest change within 2 lines (e.g. an argument on the next line)
    const near=L.map((l,i)=>[l,i]).filter(([l])=>l.t!==' '&&l[key]!=null&&Math.abs(l[key]-loc.line)<=2);
    if(!near.length)return null;
    const lo=Math.max(0,Math.min(...near.map(x=>x[1]))-1),hi=Math.min(L.length-1,Math.max(...near.map(x=>x[1]))+1);
    rows=L.slice(lo,hi+1);if(rows.length>8)rows=rows.slice(0,8);
  }
  if(!rows.some(l=>l.t!==' '))return null;
  const box=$('div',{class:'mini',title:'open in the file diff',onclick:(e)=>{e.stopPropagation();state.codeChanges=true;jumpToLine(loc.file,removed?null:loc.line,removed?loc.line:null);}});
  box.append($('div',{class:'cap',text:loc.file.split('/').pop()+' · '+h.header.split('@@')[1].trim()}));
  for(const l of rows){const r=$('div',{class:'ln '+(l.t==='+'?'add':l.t==='-'?'del':'ctx')});r.append($('span',{class:'no',text:String(l.t==='-'?l.o:l.n)}),$('span',{class:'sg',text:l.t.trim()}));const c=$('span',{class:'code'});c.append(hlLine(l.s,l.w));r.append(c);box.append(r);}
  return box;
}
function jumpToLine(file,newLine,oldLine){
  const doJump=()=>{const sel=newLine?`[data-file="${CSS.escape(file)}"][data-n="${newLine}"]`:`[data-file="${CSS.escape(file)}"][data-o="${oldLine}"]`;const tr=document.querySelector(sel);if(!tr)return;let d=tr.closest('details');if(d)d.open=true;tr.scrollIntoView({block:'center',behavior:'smooth'});tr.classList.add('hl');setTimeout(()=>tr.classList.remove('hl'),2000);};
  if(!document.querySelector('.cd')){render();setTimeout(doJump,50);}else doJump();
}
function renderCodePanel(ep){
  const wrap=$('div',{class:'cd'});
  const files=new Set();
  const walk=(nodes)=>{for(const n of nodes){if(n.location&&n.location.file)files.add(n.location.file);if(n.before&&n.before.location&&n.before.location.file)files.add(n.before.location.file);walk(n.children||[]);for(const b of (n.branches||[]))walk(b.steps||[]);}};
  if(ep.handler&&ep.handler.file)files.add(ep.handler.file);if(ep.before&&ep.before.handler&&ep.before.handler.file)files.add(ep.before.handler.file);walk(ep.flow||[]);
  const list=[...files].filter(f=>FILE_DIFFS[f]).sort();
  const h3=$('h3',{},$('span',{text:'Code changes'}),$('span',{class:'tag',text:list.length+' file'+(list.length===1?'':'s')+' touched by this endpoint',style:'font-weight:400;text-transform:none;letter-spacing:0'}));
  const sw=$('span',{class:'sw'});sw.append($('button',{class:state.sbs?'':'on',text:'Inline',onclick:()=>{state.sbs=false;render();}}),$('button',{class:state.sbs?'on':'',text:'Side by side',onclick:()=>{state.sbs=true;render();}}));h3.append(sw);wrap.append(h3);
  if(!list.length){wrap.append($('div',{class:'more',text:'No source diff for the files this endpoint touches (the change may be in files the flow does not reach, or the diff was generated with --no-code-diff).'}));}
  for(const f of list){wrap.append(renderFileDiff(FILE_DIFFS[f]));}
  const others=(DATA.changed_files||[]).filter(f=>!list.includes(f));
  if(others.length)wrap.append($('div',{class:'more',text:'Also changed in this branch (not part of this endpoint\'s flow): '+others.slice(0,12).join(', ')+(others.length>12?' … +'+(others.length-12)+' more':'')}));
  return wrap;
}
function renderFileDiff(fd){
  const det=$('details',{class:'cdf',open:true});
  const parts=fd.path.split('/');const base=parts.pop();
  let add=0,del=0;for(const h of fd.hunks)for(const l of h.lines){if(l.t==='+')add++;else if(l.t==='-')del++;}
  const sum=$('summary',{},$('span',{class:'tri',text:'▶'}),$('span',{class:'path'},$('span',{class:'dir',text:parts.length?parts.join('/')+'/':''}),base));
  if(fd.status&&fd.status!=='modified')sum.append($('span',{class:'fst '+fd.status,text:fd.status}));
  if(fd.old_path)sum.append($('span',{class:'fst',text:'was '+fd.old_path}));
  sum.append($('span',{class:'stat'},$('span',{class:'p',text:'+'+add}),' ',$('span',{class:'mnus',text:'−'+del})));
  det.append(sum);
  if(fd.truncated){det.append($('div',{class:'more',style:'padding:8px 12px',text:'Diff too large to embed; open the file in your editor.'}));return det;}
  const ide=$('div',{class:'ide'+(state.sbs?' sbs':'')});const table=$('table',{});
  for(const h of fd.hunks){
    const hr=$('tr',{class:'hunk'});hr.append($('td',{colspan:state.sbs?6:4,text:h.header}));table.append(hr);
    if(!state.sbs){for(const l of h.lines){const tr=$('tr',{class:l.t==='+'?'add':l.t==='-'?'del':'ctx'});tr.dataset.file=fd.path;if(l.o!=null)tr.dataset.o=l.o;if(l.n!=null)tr.dataset.n=l.n;tr.append($('td',{class:'no',text:l.o!=null?String(l.o):''}),$('td',{class:'no',text:l.n!=null?String(l.n):''}),$('td',{class:'sg',text:l.t.trim()}));const c=$('td',{class:'code'});c.append(hlLine(l.s,l.w));tr.append(c);table.append(tr);}}
    else{
      // pair removed/added runs so both panes stay aligned
      let i=0;const L=h.lines;
      while(i<L.length){
        if(L[i].t===' '){const l=L[i];const tr=$('tr',{class:'ctx'});tr.dataset.file=fd.path;tr.dataset.o=l.o;tr.dataset.n=l.n;const c1=$('td',{class:'code'});c1.append(hlLine(l.s));const c2=$('td',{class:'code'});c2.append(hlLine(l.s));tr.append($('td',{class:'no',text:String(l.o)}),c1,$('td',{class:'no',text:String(l.n)}),c2);table.append(tr);i++;continue;}
        let j=i;while(j<L.length&&L[j].t==='-')j++;let k=j;while(k<L.length&&L[k].t==='+')k++;
        const rem=L.slice(i,j),ad=L.slice(j,k);const rows=Math.max(rem.length,ad.length);
        for(let r=0;r<rows;r++){const a=rem[r],b=ad[r];const tr=$('tr',{class:(a&&b)?'mod':a?'del':'add'});tr.dataset.file=fd.path;if(a)tr.dataset.o=a.o;if(b)tr.dataset.n=b.n;
          if(a){const c=$('td',{class:'code',style:'background:#FBE9E7'});c.append(hlLine(a.s,a.w));tr.append($('td',{class:'no',text:String(a.o),style:'background:#F4D6D2;color:#8C3A33'}),c);}else tr.append($('td',{class:'no empty'}),$('td',{class:'code empty'}));
          if(b){const c=$('td',{class:'code',style:'background:#E6F4EA'});c.append(hlLine(b.s,b.w));tr.append($('td',{class:'no',text:String(b.n),style:'background:#D8ECDF;color:#4F7C5E'}),c);}else tr.append($('td',{class:'no empty'}),$('td',{class:'code empty'}));
          table.append(tr);}
        i=k;if(j===i&&k===i)i++;
      }
    }
  }
  ide.append(table);det.append(ide);return det;
}

// ---------------------------------------------------------------- before / after diagrams (side by side)
function tokens(t){return String(t||'').split(/(\s+|[(),.;:=<>!&|+\-*\/{}\[\]"'`?])/).filter(x=>x!=='');}
function tokenDiff(a,b){
  const A=tokens(a),B=tokens(b);const n=A.length,m=B.length;
  if(n*m>40000)return [A.map(t=>({t,c:false})),B.map(t=>({t,c:false}))];
  const dp=Array.from({length:n+1},()=>new Uint16Array(m+1));
  for(let i=n-1;i>=0;i--)for(let j=m-1;j>=0;j--)dp[i][j]=A[i]===B[j]?dp[i+1][j+1]+1:Math.max(dp[i+1][j],dp[i][j+1]);
  const ra=[],rb=[];let i=0,j=0;
  while(i<n&&j<m){if(A[i]===B[j]){ra.push({t:A[i],c:false});rb.push({t:B[j],c:false});i++;j++;}else if(dp[i+1][j]>=dp[i][j+1]){ra.push({t:A[i],c:true});i++;}else{rb.push({t:B[j],c:true});j++;}}
  while(i<n)ra.push({t:A[i++],c:true});while(j<m)rb.push({t:B[j++],c:true});
  return [ra,rb];
}
function segEl(cls,segs){const el=$('div',{class:cls});for(const sg of segs){if(sg.c&&sg.t.trim())el.append($('span',{class:'w',text:sg.t}));else el.append(document.createTextNode(sg.t));}return el;}
function sideData(n,side){
  const ch=n.change||'same';
  if(side==='left'){
    if(ch==='added')return null;
    if(ch==='modified'&&n.before){const b=n.before;return Object.assign({},n,{label:b.label||n.label,auto_label:b.auto_label||b.label,detail:b.detail!=null?b.detail:n.detail,condition:b.condition!=null?b.condition:n.condition,outcome:b.outcome||n.outcome,code:b.code||n.code,location:b.location||n.location,target:b.target||n.target,_isBefore:true});}
    return n;
  }
  if(ch==='removed'||ch==='moved-from')return null;
  return n;
}
function dgNode2(n,side,extraClass){
  const d=sideData(n,side);const change=n.change||'same';
  const other=side==='left'?n:(n.change==='modified'?sideData(n,'left'):n);
  const el=$('div',{class:'dg-node k-'+n.kind+' c-'+(change==='modified'?'modified':change==='same'||change==='moved'?'same':change)+(side==='left'?' side-l':' side-r')+(extraClass?' '+extraClass:'')+((change==='same'||change==='moved')&&MODE==='diff'&&state.focusChanges&&!hasChangeIn(n)?' dim':'')});
  el.dataset.sid=n.id||'';el.dataset.depth=String(n._depth||0);el.dataset.side=side;
  if(change!=='same'&&change!=='moved-from')el.append($('span',{class:'chg '+change,text:change==='added'?'+':change==='removed'?'−':change==='modified'?'~':'⇅'}));
  const t=$('div',{class:'t'},$('span',{class:'g',text:GLYPH[n.kind]||'·'}));
  if(change==='modified'&&d.label!==other.label){const [la,lb]=tokenDiff(sideData(n,'left').label,n.label);t.append(segEl('',side==='left'?la:lb).firstChild?segEl('',side==='left'?la:lb):$('span',{text:d.label}));}
  else t.append($('span',{text:d.label||''}));
  if(d.outcome&&d.outcome.status)t.append($('span',{class:'st '+statusClass(d.outcome.status),text:d.outcome.status,style:'margin-left:auto'}));
  el.append(t);
  // facts: pair before/after and highlight differing tokens
  const mine=factsFor(Object.assign({},d,{change:'same'}));
  if(change==='modified'){
    const theirs=factsFor(Object.assign({},side==='left'?n:sideData(n,'left'),{change:'same'}));
    mine.forEach((f,i)=>{const o=theirs[i];if(o&&o.t!==f.t){const [fa,fb]=tokenDiff(side==='left'?f.t:o.t,side==='left'?o.t:f.t);el.append(segEl('f '+f.c,side==='left'?fa:fb));}else el.append($('div',{class:'f '+f.c,text:f.t}));});
  } else for(const f of mine)el.append($('div',{class:'f '+f.c,text:f.t}));
  if(side==='right'&&state.codeChanges&&change!=='same'){const m=miniDiff(n);if(m){el.append(m);el.style.borderRadius='12px';}}
  if(d.location&&d.location.file&&n.kind!=='return'&&n.kind!=='throw')el.append($('div',{class:'loc',text:d.location.file.split('/').pop()+(d.location.line?':'+d.location.line:'')}));
  return el;
}
function ghost(n,side,text){const g=$('div',{class:'dg-ghost '+(side==='left'?'ga':'gr'),text:text||(side==='left'?'not in base':'removed')});g.dataset.sid=n.id||'';g.dataset.depth=String(n._depth||0);g.dataset.side=side;return g;}
function dgColumn2(nodes,depth,side){
  const col=$('div',{class:'dg-col'});
  const vis=nodes.filter(n=>n.change!=='moved-from'&&!(state.hideLog&&n.kind==='log'));
  vis.forEach((n,i)=>{n._depth=depth;if(i>0)dgLink(col);dgAppend2(n,col,depth,side);});
  return col;
}
function dgAppend2(n,col,depth,side){
  const d=sideData(n,side);
  const kids=(n.children||[]).filter(k=>!(state.hideLog&&k.kind==='log'));
  const isWrapper=(n.tags||[]).includes('wrapper');
  const expand=kids.length&&!isWrapper&&(state.expandAll||(state.focusChanges?(hasChangeIn(n)&&n.change==='same')||(n.change!=='same'&&depth<1):depth<1));
  if(kids.length&&expand){
    const g=$('div',{class:'dg-group c-'+(n.change||'same')});g.dataset.gid=n.id||'';g.dataset.depth=String(depth);g.dataset.side=side;
    g.append(d?dgNode2(n,side,'hd'):ghost(n,side));
    dgLink(g);
    g.append(dgColumn2(kids,depth+1,side));
    col.append(g);
  } else {
    if(d){const node=dgNode2(n,side,'');if(kids.length&&!isWrapper)node.append($('div',{class:'f meta',text:countAll(kids)+' steps inside'+(n.nested_changes?' · '+[n.nested_changes.added?'+'+n.nested_changes.added:'',n.nested_changes.removed?'−'+n.nested_changes.removed:'',n.nested_changes.modified?'~'+n.nested_changes.modified:''].filter(Boolean).join(' ')+' changed':'')}));col.append(node);}
    else col.append(ghost(n,side));
  }
  if((n.branches||[]).length){
    col.append($('div',{class:'dg-cstub'}));
    const arms=$('div',{class:'dg-arms'});
    const isIf=(n.kind==='branch'&&String(n.detail||'').startsWith('if ('));
    const hasElse=(n.branches||[]).some(b=>b.label==='Otherwise');
    const list=(isIf&&!hasElse)?n.branches.concat([{label:'no',implicit:true,steps:[],exits:false,change:'same'}]):n.branches;
    let anyContinue=false;
    list.forEach((arm,ai)=>{
      const pos=list.length===1?'only':ai===0?'first':ai===list.length-1?'last':'mid';
      const armGhost=(arm.change==='added'&&side==='left')||(arm.change==='removed'&&side==='right');
      const a=$('div',{class:'dg-arm '+pos+(armGhost?' ghost':'')});a.dataset.aid=(n.id||'')+':'+ai;a.dataset.depth=String(depth);a.dataset.side=side;
      const firstIf=(ai===0&&n.kind==='branch'&&String(n.detail||'').startsWith('if ('));
      let labelText=firstIf?'yes':arm.label;
      if(side==='left'&&arm.change==='modified'&&arm.before&&arm.before.label&&!firstIf)labelText=arm.before.label;
      const lbl=$('div',{class:'lbl '+(armGhost?'':(arm.change==='modified'?'modified':arm.change||'same')),text:labelText});
      if(arm.implicit)lbl.style.opacity='.75';
      a.append(lbl);
      const steps=(arm.steps||[]).filter(s=>s.change!=='moved-from');
      if(armGhost){a.append($('div',{class:'none',text:side==='left'?'(branch added)':'(branch removed)'}));}
      else if(arm.implicit){a.append($('div',{class:'none',text:'continue ↓'}));}
      else if(steps.length){a.append($('div',{class:'dg-cstub'}));a.append(dgColumn2(steps,depth+1,side));}
      else a.append($('div',{class:'none',text:'nothing else happens'}));
      if(!armGhost&&arm.exits)a.append($('div',{class:'exitmark',text:'▣ ends here'}));else if(!armGhost)anyContinue=true;
      arms.append(a);
    });
    col.append(arms);
    if(anyContinue)col.append($('div',{class:'dg-merge'}));
  }
}
function dgInputs2(ep,side){
  const props=side==='left'?((ep.before&&ep.before.properties)||ep.properties||{}):(ep.properties||{});
  const changes={};for(const c of (ep.property_changes||[]))changes[c.key]=c;
  const box=$('div',{class:'dg-io'});box.dataset.sid='__inputs';box.dataset.depth='0';box.dataset.side=side;
  const idText=side==='left'&&ep.before&&ep.before.id?ep.before.id:ep.id;
  box.append($('div',{class:'t'},$('span',{class:'m '+ep.method,text:ep.method}),$('span',{html:pathHtml(idText.replace(/^\w+\s+/,''))})));
  const fmt=v=>Array.isArray(v)?v.join(', '):(typeof v==='boolean'?(v?'yes':'no'):String(v));
  const rows=[['auth','auth'],['validation','validation'],['body','body'],['path_params','path'],['query_params','query'],['headers','headers'],['form','form'],['status','declared status'],['responses','responds'],['feature_flags','feature flags'],['returns','returns']];
  for(const [k,label] of rows){
    const c=changes[k];const v=props[k];
    if(c){const mine=side==='left'?c.before:c.after;if(mine==null||(Array.isArray(mine)&&!mine.length)){box.append($('div',{class:'f'},$('b',{text:label+': '}),$('span',{class:side==='left'?'now':'now',text:side==='left'?'(not set)':'(removed)',style:'color:var(--modified)'})));continue;}
      const [fa,fb]=tokenDiff(fmt(c.before==null?'':c.before),fmt(c.after==null?'':c.after));const segs=side==='left'?fa:fb;const line=$('div',{class:'f'});line.append($('b',{text:label+': '}));for(const sg of segs){if(sg.c&&sg.t.trim())line.append($('span',{class:'now',text:sg.t,style:'background:'+(side==='left'?'rgba(180,35,24,.18)':'rgba(30,127,79,.2)')+';border-radius:2px'}));else line.append(document.createTextNode(sg.t));}box.append(line);continue;}
    if(v==null||v===false||(Array.isArray(v)&&!v.length))continue;
    box.append($('div',{class:'f'},$('b',{text:label+': '}),fmt(v)));
  }
  if(!box.querySelector('.f'))box.append($('div',{class:'f',text:'no inputs detected'}));
  return box;
}
function renderSideBySide(ep){
  const wrap=$('div',{class:'sbs2'});
  const b=DATA.base||{},h=DATA.head||{};
  const mk=(side)=>{
    const sd=$('div',{class:'side '+side});
    sd.append($('div',{class:'sh'},$('span',{text:side==='left'?'Before':'After'}),$('span',{class:'ref',text:side==='left'?((b.ref||b.branch||'base')+(b.commit?' @'+b.commit:'')):((h.branch||h.ref||'working tree')+(h.commit?' @'+h.commit:'')+(h.dirty?' + uncommitted':''))})));
    if((ep.status==='removed'&&side==='right')||(ep.status==='added'&&side==='left')){sd.append($('div',{class:'dg-ghost big '+(side==='left'?'ga':'gr'),text:side==='left'?'This endpoint does not exist in the base branch':'This endpoint no longer exists in this branch'}));return sd;}
    const dg=$('div',{class:'dg'});const col=$('div',{class:'dg-col'});
    col.append(dgInputs2(ep,side));dgLink(col);col.append(dgColumn2(ep.flow||[],0,side));dg.append(col);sd.append(dg);return sd;
  };
  wrap.append(mk('left'),mk('right'));
  const leg=$('div',{class:'dg-legend',style:'grid-column:1/3'});
  leg.append($('span',{class:'lg-c',text:'condition'}),$('span',{class:'lg-e',text:'response'}),$('span',{class:'lg-x',text:'failure / exception'}),$('span',{class:'lg-a',text:'added (right only)'}),$('span',{class:'lg-r',text:'removed (left only)'}),$('span',{class:'lg-m',text:'modified — differing values highlighted on both sides'}));
  leg.append($('span',{class:'note',text:'rows are aligned step by step: a dashed placeholder marks where a step exists on the other side only; untouched steps are dimmed'}));
  wrap.append(leg);
  return wrap;
}
function scheduleEqualize(root){requestAnimationFrame(()=>equalize(root));}
function equalize(root){
  const els=[...root.querySelectorAll('[data-sid],[data-gid],[data-aid]')];
  const groups=new Map();
  for(const el of els){const key=(el.dataset.sid!=null?'s:'+el.dataset.sid:el.dataset.gid!=null?'g:'+el.dataset.gid:'a:'+el.dataset.aid)+'|'+ (el.closest('.sbs2')?[...root.querySelectorAll('.sbs2')].indexOf(el.closest('.sbs2')):'x');if(!groups.has(key))groups.set(key,[]);groups.get(key).push(el);}
  const pairs=[...groups.values()].filter(g=>g.length===2&&g[0].dataset.side!==g[1].dataset.side).map(g=>({g,depth:+g[0].dataset.depth||0,kind:g[0].dataset.sid!=null?0:g[0].dataset.gid!=null?2:1}));
  pairs.sort((x,y)=>y.depth-x.depth||x.kind-y.kind);
  for(const {g} of pairs){for(const el of g)el.style.minHeight='';const hgt=Math.max(g[0].getBoundingClientRect().height,g[1].getBoundingClientRect().height);for(const el of g)el.style.minHeight=hgt+'px';}
}
function countAll(nodes){let c=0;for(const n of nodes){c++;c+=countAll(n.children||[]);for(const b of (n.branches||[]))c+=countAll(b.steps||[]);}return c;}
document.addEventListener('keydown',e=>{
  if(e.target&&(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')){if(e.key==='Escape')e.target.blur();return;}
  const eps=DATA.endpoints.filter(visible);const i=eps.findIndex(x=>x.id===state.sel);
  if(e.key==='j'||e.key==='ArrowDown'){if(i<eps.length-1){state.sel=eps[i+1].id;render();}e.preventDefault();}
  else if(e.key==='k'||e.key==='ArrowUp'){if(i>0){state.sel=eps[i-1].id;render();}e.preventDefault();}
  else if(e.key==='/'){e.preventDefault();const q=document.getElementById('q');if(q)q.focus();}
  else if(e.key==='c'){state.showCode=!state.showCode;render();}
  else if(e.key==='e'){state.expandAll=!state.expandAll;render();}
  else if(e.key==='d'){state.view=state.view==='diagram'?'ledger':'diagram';render();}
  else if(e.key==='x'&&MODE==='diff'){state.codeChanges=!state.codeChanges;render();}
});
if(location.hash){const id=decodeURIComponent(location.hash.slice(1));if(DATA.endpoints.some(e=>e.id===id))state.sel=id;}
if(!state.sel){const first=DATA.endpoints.find(e=>MODE==='diff'?isChanged(e):true)||DATA.endpoints[0];state.sel=first?first.id:null;}
render();
})();
</script>
</body>
</html>
"""
