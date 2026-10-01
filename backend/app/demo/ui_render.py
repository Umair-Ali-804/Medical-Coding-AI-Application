"""HTML renderers for the workbench. Pure presentation: they take the results the services already
produce (code_note / scrub_claim / lookup / guidelines / reference status) and return HTML strings."""

from __future__ import annotations

import re
from datetime import datetime
from html import escape

from app.demo.ui_theme import BRAND, PRODUCT, data_uri

SEV = {"deny": ("deny", "Denial risk"), "review": ("warn", "Review"), "info": ("info", "Note")}
STATUS = {
    "clean": ("ok", "Clean"),
    "review": ("warn", "Needs review"),
    "likely_denial": ("deny", "Likely denial"),
}


def e(v: object) -> str:
    return escape("" if v is None else str(v))


def _sys_chip(system: str) -> str:
    cls = {"ICD-10-CM": "dx", "ICD-10-PCS": "px", "HCPCS": "hc", "CPT": "px"}.get(system, "")
    return f'<span class="sys {cls}">{e(system)}</span>'


def _pill(text: str, cls: str = "") -> str:
    return f'<span class="pill {cls}">{e(text)}</span>'


def _validation_pill(v: str) -> str:
    return _pill(v, {"passed": "ok", "flagged": "warn", "rejected": "deny"}.get(v, ""))


def _route_pill(r: str) -> str:
    return _pill(
        {"standard": "standard", "mandatory": "coder review", "system_rejected": "rejected"}.get(r, r),
        {"standard": "ok", "mandatory": "info", "system_rejected": "deny"}.get(r, ""),
    )


def _risk_cls(score: int) -> str:
    return "ok" if score < 20 else "warn" if score < 60 else "deny"


def _meter(pct: float, cls: str) -> str:
    color = {"ok": "var(--ax-ok)", "warn": "var(--ax-warn)", "deny": "var(--ax-deny)"}.get(
        cls, "var(--ax-silver)"
    )
    return f'<div class="meter"><i style="width:{max(2, min(100, pct)):.0f}%;background:{color}"></i></div>'


def _sec(title: str, count: int | None = None) -> str:
    c = f'<span class="count">{count}</span>' if count is not None else ""
    return f'<h3 class="sec">{e(title)}{c}</h3>'


def wrap(inner: str) -> str:
    return f'<div class="ax">{inner}</div>'


# ------------------------------------------------------------------ chrome


def header_html(llm: str, sets_valid_to: str | None) -> str:
    mark, word = data_uri("logo_mark.png"), data_uri("logo_wordmark.png")
    valid = f'<span class="ax-chip">Code sets valid to {e(sets_valid_to)}</span>' if sets_valid_to else ""
    llm_chip = "LLM coder" if llm == "openrouter" else "Offline coder"
    return f"""
<div class="ax-header">
  <div class="ax-brand">
    <img class="mark" src="{mark}" alt="{BRAND} logo">
    <img class="word" src="{word}" alt="{BRAND} Automations">
  </div>
  <div class="ax-divider"></div>
  <div class="ax-product">
    <div class="name">{PRODUCT}</div>
    <div class="tag">Clinical note to coder-approved codes and a clean claim, with evidence and guideline citations.</div>
  </div>
  <div class="ax-head-meta">
    <span class="ax-chip"><span class="dot"></span>Online</span>
    <span class="ax-chip">{llm_chip}</span>
    {valid}
  </div>
</div>
<div class="ax-flow">
  <span><b>01</b>Clinical note</span><span><b>02</b>Evidence-backed codes</span><span><b>03</b>Guideline citations</span>
  <span><b>04</b>Draft claim</span><span><b>05</b>Pre-bill denial check</span>
</div>"""


def footer_html() -> str:
    return f"""
<div class="ax-footer">
  <img src="{data_uri("logo_wordmark.png")}" alt="{BRAND}">
  <span>Decision support only: a certified coder reviews and approves every code. Use synthetic or de-identified notes.</span>
</div>"""


def empty_html(title: str, text: str) -> str:
    return wrap(
        f'<div class="empty"><img src="{data_uri("logo_mark.png")}" alt=""><div class="t">{e(title)}</div>'
        f"<div>{e(text)}</div></div>"
    )


def error_html(msg: str) -> str:
    return wrap(f'<div class="error"><b>Could not complete the request.</b><br>{e(msg)}</div>')


# ------------------------------------------------------------------ issues (shared)


def _issues_html(issues: list, not_checked: list[str]) -> str:
    if not issues:
        body = '<div class="okbox">No denial risks found against the loaded reference data.</div>'
    else:
        items = []
        order = {"deny": 0, "review": 1, "info": 2}
        for i in sorted(issues, key=lambda x: (order.get(x.severity, 3), x.line or 0)):
            cls, label = SEV.get(i.severity, ("", i.severity))
            where = f"Line {i.line}" if i.line else "Claim"
            carc = f"CARC {i.carc}" + (f" / {i.rarc}" if i.rarc else "") if i.carc else ""
            items.append(
                f'<div class="issue {"review" if i.severity == "review" else i.severity}">'
                f'<div>{_pill(label, cls)}<div class="faint" style="font-size:11.5px;margin-top:6px">{e(where)}</div></div>'
                f'<div><div class="msg">{e(i.message)}</div>'
                + (f'<div class="fix">Fix: {e(i.fix)}</div>' if i.fix else "")
                + (f'<div class="src">Source: {e(i.source)}</div>' if i.source else "")
                + "</div>"
                + (f'<span class="carc">{e(carc)}</span>' if carc else "<span></span>")
                + "</div>"
            )
        body = f'<div class="issues">{"".join(items)}</div>'
    if not_checked:
        body += (
            '<ul class="notes"><li><b>Not checked</b> (reference data not loaded):</li>'
            + "".join(f"<li>{e(n)}</li>" for n in not_checked)
            + "</ul>"
        )
    return body


def _kpis(scrub, extra_label: str, extra_value: str, extra_sub: str, codes_value: str, codes_sub: str) -> str:
    cls, label = STATUS.get(scrub.status, ("", scrub.status))
    n_deny = sum(1 for i in scrub.issues if i.severity == "deny")
    n_rev = sum(1 for i in scrub.issues if i.severity == "review")
    rc = _risk_cls(scrub.risk_score)
    return f"""
<div class="kpis">
  <div class="kpi {cls}"><div class="l">Claim status</div><div class="v">{e(label)}</div>
    <div class="s">{n_deny} denial risk{"s" if n_deny != 1 else ""} · {n_rev} to review</div></div>
  <div class="kpi {rc}"><div class="l">Denial risk</div><div class="v">{scrub.risk_score}%</div>{_meter(scrub.risk_score, rc)}</div>
  <div class="kpi"><div class="l">Codes</div><div class="v">{e(codes_value)}</div><div class="s">{e(codes_sub)}</div></div>
  <div class="kpi"><div class="l">{e(extra_label)}</div><div class="v">{e(extra_value)}</div><div class="s">{e(extra_sub)}</div></div>
</div>"""


# ------------------------------------------------------------------ 1. code a note


def note_html(out: dict, meta: dict) -> str:
    sugs, scrub, claim = out["suggestions"], out["scrub"], out["claim"]
    n_dx = sum(1 for s in sugs if s["code_system"] == "ICD-10-CM")
    n_px = len(sugs) - n_dx
    money = f"${scrub.dollars_at_risk:,.2f} at risk" if scrub.dollars_at_risk else "no charges entered"
    route = "Coder review" if out["route"] == "mandatory" else "Standard"
    parts = [
        _kpis(scrub, "Review route", route, money, f"{n_dx} + {n_px}", "diagnoses + procedures"),
    ]

    # suggested codes
    rows = []
    for s in sugs:
        conf = float(s["confidence"]) * 100
        ev = " … ".join(s["evidence"])[:260]
        rows.append(
            "<tr>"
            f'<td class="num">{s["sequence"]}</td>'
            f"<td>{_sys_chip(s['code_system'])}</td>"
            f'<td><span class="code">{e(s["code"])}</span></td>'
            f'<td><div class="desc">{e(s["description"])}</div>'
            + (f'<div class="ev">“{e(ev)}”</div>' if ev else "")
            + "".join(f'<div class="iss">⚠ {e(i)}</div>' for i in s["issues"])
            + "</td>"
            f'<td class="conf"><span class="pct">{conf:.1f}%</span><div class="bar"><i style="width:{conf:.0f}%"></i></div></td>'
            f"<td>{_validation_pill(s['validation'])}</td>"
            f"<td>{_route_pill(s['route'])}</td>"
            "</tr>"
        )
    parts.append(_sec("Suggested codes", len(sugs)))
    if rows:
        parts.append(
            '<div class="tbl"><table class="t"><thead><tr><th>#</th><th>System</th><th>Code</th><th>Description &amp; evidence</th>'
            "<th>Confidence</th><th>Validation</th><th>Route</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div>"
        )
    else:
        parts.append('<div class="card muted">No codes could be suggested for this note.</div>')

    proc_nc = [n for n in out.get("not_coded", []) if n.get("reason", "").startswith("procedure")]
    if proc_nc:
        parts.append(
            '<ul class="notes"><li><b>Procedures needing manual coding:</b></li>'
            + "".join(f"<li>{e(n['entity'])}: {e(n['reason'])}</li>" for n in proc_nc)
            + "</ul>"
        )

    # draft claim
    dx = "".join(
        f'<div class="dx"><span class="ptr">{i}</span><span class="code">{e(c)}</span>'
        f'<span class="muted" style="font-size:12.5px">{e(_desc_for(sugs, c))}</span></div>'
        for i, c in enumerate(claim.diagnoses, 1)
    )
    lines = "".join(
        f'<tr><td class="num">{n}</td><td>{_sys_chip(x.system)}</td><td><span class="code">{e(x.code)}</span></td>'
        f'<td class="mono">{x.units:g}</td><td class="muted">{e(x.description or "")}</td></tr>'
        for n, x in enumerate(claim.lines, 1)
    )
    parts.append(_sec(f"Draft claim · {claim.claim_type} · DOS {claim.date_of_service}"))
    parts.append(
        '<div class="two">'
        f'<div class="card"><div class="lbl">Diagnoses (sequence = pointer)</div><div class="dxlist" style="margin-top:10px">{dx or "<span class=muted>none</span>"}</div></div>'
        + (
            '<div class="tbl"><table class="t"><thead><tr><th>Line</th><th>System</th><th>Code</th><th>Units</th><th>Description</th></tr></thead>'
            f"<tbody>{lines}</tbody></table></div>"
            if lines
            else '<div class="card muted">No procedure lines. Add the E/M visit level and any procedures during coder review.</div>'
        )
        + "</div>"
    )

    # claim check
    parts.append(_sec("Pre-bill claim check", len(scrub.issues)))
    parts.append(_issues_html(scrub.issues, scrub.not_checked))

    # citations
    cites = []
    for s in sugs:
        for g in s["guidelines"][:2]:
            cites.append(
                f'<details class="cite"><summary><span class="code">{e(s["code"])}</span>'
                f'<span class="ref">{e(g["citation"])} · {e(g["title"])}</span></summary>'
                f'<div class="body">{e(g["snippet"])}</div></details>'
            )
    parts.append(_sec("Official guideline citations", len(cites)))
    parts.append(
        "".join(cites)
        or '<div class="card muted">No guideline passages matched (are the guideline PDFs loaded?).</div>'
    )

    # entities
    ents = out["entities"]
    coded = [
        x
        for x in ents
        if x["status"] == "affirmed" and x["category"] in ("condition", "symptom", "finding", "procedure")
    ]
    excluded = [x for x in ents if x["status"] != "affirmed"]

    def chips(items: list[dict], neg: bool) -> str:
        return (
            "".join(
                f'<span class="chip {"neg" if neg and x["status"] == "negated" else ""}">{e(x["text"])}'
                f"<small>{e(x['status'] if neg else x['category'])}</small></span>"
                for x in items[:40]
            )
            or '<span class="faint">none</span>'
        )

    parts.append(_sec("Clinical understanding"))
    parts.append(
        '<div class="ents">'
        f'<div class="card"><div class="lbl">Documented for this patient</div><div class="chips">{chips(coded, False)}</div></div>'
        f'<div class="card"><div class="lbl">Not coded: negated, family, uncertain or historical</div><div class="chips">{chips(excluded, True)}</div></div>'
        "</div>"
    )
    parts.append(
        f'<div class="foot"><span>Document {e(out["document_id"][:8])}</span><span>Encounter: {e(meta.get("encounter"))}</span>'
        f"<span>Patient: {e(meta.get('sex') or 'sex n/a')}, {e(meta.get('age') if meta.get('age') is not None else 'age n/a')}</span>"
        f"<span>Generated {datetime.now():%Y-%m-%d %H:%M}</span></div>"
    )
    return wrap("".join(parts))


def _desc_for(sugs: list[dict], code: str) -> str:
    return next((s["description"] for s in sugs if s["code"] == code), "")


# ------------------------------------------------------------------ 2. claim scrubber


def claim_html(res, claim_in: dict) -> str:
    money = f"${res.dollars_at_risk:,.2f}" if res.dollars_at_risk else "$0.00"
    lines = claim_in.get("lines", [])
    parts = [
        _kpis(
            res,
            "Dollars at risk",
            money,
            "from lines with denial risks",
            f"{len(claim_in.get('diagnoses', []))} + {len(lines)}",
            "diagnoses + service lines",
        ),
        _sec("Findings", len(res.issues)),
        _issues_html(res.issues, res.not_checked),
    ]
    if res.references:
        refs = "".join(f"<span>{e(k)} {e(v)}</span>" for k, v in sorted(res.references.items()))
        parts.append(f'<div class="foot">{refs}</div>')
    return wrap("".join(parts))


# ------------------------------------------------------------------ 3. code lookup


def lookup_html(data: dict) -> str:
    if data.get("error"):
        return empty_html("Nothing to show", data["error"])
    rows = "".join(
        f'<tr><td><span class="code">{e(r["code"])}</span></td><td>{e(r["description"])}</td>'
        f"<td>{_pill('billable', 'ok') if r['billable'] else _pill('header / inactive', 'warn')}</td></tr>"
        for r in data["results"]
    )
    parts = []
    top = data.get("top")
    if top:
        kv = "".join(f'<div class="k">{e(k)}</div><div>{e(v)}</div>' for k, v in top["facts"])
        parts.append(
            '<div class="card detail">'
            f'<div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap"><span class="big-code">{e(top["code"])}</span>'
            f"{_sys_chip(data['system'])}{_pill('billable', 'ok') if top['billable'] else _pill('not billable', 'warn')}</div>"
            f'<div class="desc" style="font-size:16px">{e(top["description"])}</div>'
            f'<div class="kv">{kv}</div></div>'
        )
    parts.append(_sec(f"Results · {data['system']}", len(data["results"])))
    parts.append(
        '<div class="tbl"><table class="t"><thead><tr><th>Code</th><th>Description</th><th>Status</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
        if rows
        else '<div class="card muted">No matching codes.</div>'
    )
    return wrap("".join(parts))


# ------------------------------------------------------------------ 4. guidelines


_ITEM = re.compile(r"^(\(?[a-z0-9]{1,2}[.)]\s|Example|Note|Exception)", re.IGNORECASE)


def _reflow(text: str) -> str:
    """Join the PDF's hard line breaks into paragraphs; keep list items and examples on new lines."""
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if out and not _ITEM.match(line):
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    return "\n".join(out)


def guidelines_html(hits: list) -> str:
    if not hits:
        return empty_html(
            "No passages found", "Try other words, or check that the guideline PDFs are loaded."
        )
    cards = "".join(
        f'<div class="gl"><div class="h">{_pill(h.system, "info")}<span class="code" style="font-size:13px">{e(h.ref)}</span>'
        f'<span class="title">{e(h.title)}</span><span class="faint" style="margin-left:auto;font-size:12px">'
        f'{e(h.citation())}</span></div><div class="txt">{e(_reflow(h.text)[:1800])}</div></div>'
        for h in hits
    )
    return wrap(_sec("Official guideline passages", len(hits)) + cards)


# ------------------------------------------------------------------ 5. reference data


def _invalid_label(r: dict, dos: str) -> str:
    return "expired" if r["to"] != "-" and dos > r["to"] else "not yet valid"


def reference_html(data: dict) -> str:
    rows = "".join(
        f'<tr><td><span class="code" style="font-size:13px">{e(r["set"])}</span></td><td class="mono">{e(r["version"])}</td>'
        f'<td class="mono">{r["rows"]:,}</td><td class="mono">{e(r["from"])}</td><td class="mono">{e(r["to"])}</td>'
        f"<td>{_pill('valid', 'ok') if r['valid'] else _pill(_invalid_label(r, data['dos']), 'deny')}</td></tr>"
        for r in data["sets"]
    )
    n_bad = sum(1 for r in data["sets"] if not r["valid"])
    parts = [
        '<div class="kpis">'
        f'<div class="kpi {"deny" if n_bad else "ok"}"><div class="l">Reference sets</div><div class="v">{len(data["sets"]) - n_bad}/{len(data["sets"])}</div>'
        f'<div class="s">valid on {e(data["dos"])}</div></div>'
        f'<div class="kpi"><div class="l">Coder engine</div><div class="v" style="font-size:18px">{e(data["llm"])}</div><div class="s">LLM provider</div></div>'
        f'<div class="kpi"><div class="l">Embeddings</div><div class="v" style="font-size:18px">{e(data["embed"])}</div><div class="s">semantic search</div></div>'
        f'<div class="kpi"><div class="l">Vector store</div><div class="v" style="font-size:18px">{e(data["vectors"])}</div><div class="s">index storage</div></div>'
        "</div>",
        _sec("Loaded reference data", len(data["sets"])),
        '<div class="tbl"><table class="t"><thead><tr><th>Set</th><th>Version</th><th>Rows</th><th>Valid from</th><th>Valid to</th><th>Status</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>",
    ]
    if data["warnings"]:
        parts.append(_sec("Warnings", len(data["warnings"])))
        parts.append(
            '<div class="issues">'
            + "".join(
                f'<div class="issue review"><div>{_pill("Expired", "warn")}</div><div class="msg">{e(w)}</div><span></span></div>'
                for w in data["warnings"]
            )
            + "</div>"
        )
    if data["missing"]:
        parts.append(_sec("Not loaded yet", len(data["missing"])))
        parts.append('<ul class="notes">' + "".join(f"<li>{e(m)}</li>" for m in data["missing"]) + "</ul>")
    return wrap("".join(parts))
