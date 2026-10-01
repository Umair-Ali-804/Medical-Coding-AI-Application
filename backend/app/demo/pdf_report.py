"""Branded PDF exports (AAXIS AI) of what the workbench shows: coded note, claim check, code lookup.

Presentation only - built from the same result objects the UI renders. Uses reportlab.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.demo.ui_theme import BRAND, PRODUCT, asset_path

INK = colors.HexColor("#141417")
MUTED = colors.HexColor("#5d5e66")
FAINT = colors.HexColor("#8b8c94")
RULE = colors.HexColor("#d9dae0")
ZEBRA = colors.HexColor("#f5f5f7")
BAND = colors.HexColor("#0b0b0d")
SILVER = colors.HexColor("#c9cad0")
SEV_COLOR = {
    "deny": colors.HexColor("#c0392b"),
    "review": colors.HexColor("#b7791f"),
    "info": colors.HexColor("#2f6fad"),
}
STATUS_COLOR = {
    "clean": colors.HexColor("#1f8a5b"),
    "review": colors.HexColor("#b7791f"),
    "likely_denial": colors.HexColor("#c0392b"),
}
STATUS_LABEL = {"clean": "Clean", "review": "Needs review", "likely_denial": "Likely denial"}

PAGE_W, PAGE_H = letter
MARGIN = 16 * mm
BAND_H = 24 * mm

_ss = getSampleStyleSheet()
S = {
    "h1": ParagraphStyle(
        "h1",
        parent=_ss["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=15,
        textColor=INK,
        spaceAfter=2,
        spaceBefore=0,
    ),
    "h2": ParagraphStyle(
        "h2",
        parent=_ss["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=10.5,
        textColor=INK,
        spaceBefore=12,
        spaceAfter=6,
        leading=13,
    ),
    "body": ParagraphStyle(
        "body", parent=_ss["BodyText"], fontName="Helvetica", fontSize=8.8, leading=11.6, textColor=INK
    ),
    "small": ParagraphStyle(
        "small", parent=_ss["BodyText"], fontName="Helvetica", fontSize=7.8, leading=10, textColor=MUTED
    ),
    "cell": ParagraphStyle(
        "cell", parent=_ss["BodyText"], fontName="Helvetica", fontSize=8.2, leading=10.4, textColor=INK
    ),
    "cellb": ParagraphStyle(
        "cellb", parent=_ss["BodyText"], fontName="Helvetica-Bold", fontSize=8.2, leading=10.4, textColor=INK
    ),
    "code": ParagraphStyle(
        "code", parent=_ss["BodyText"], fontName="Courier-Bold", fontSize=8.8, leading=10.4, textColor=INK
    ),
    "th": ParagraphStyle(
        "th", parent=_ss["BodyText"], fontName="Helvetica-Bold", fontSize=7.2, leading=9, textColor=MUTED
    ),
    "mono": ParagraphStyle(
        "mono", parent=_ss["Code"], fontName="Courier", fontSize=7.6, leading=9.6, textColor=INK
    ),
    "right": ParagraphStyle(
        "right",
        parent=_ss["BodyText"],
        fontName="Helvetica",
        fontSize=8,
        textColor=SILVER,
        alignment=TA_RIGHT,
    ),
}


def _p(text: object, style: str = "cell") -> Paragraph:
    return Paragraph(escape("" if text is None else str(text)).replace("\n", "<br/>"), S[style])


def _colored(text: str, color: colors.Color, bold: bool = True) -> Paragraph:
    font = "Helvetica-Bold" if bold else "Helvetica"
    return Paragraph(f'<font name="{font}" color="{color.hexval()}">{escape(text)}</font>', S["cell"])


def _table(header: list[str], rows: list[list], widths: list[float]) -> Table:
    data = [[_p(h.upper(), "th") for h in header], *rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    t.setStyle(TableStyle(style))
    return t


def _kv(pairs: list[tuple[str, object]], width: float) -> Table:
    cells, row = [], []
    for k, v in pairs:
        row.append([_p(k.upper(), "th"), v if isinstance(v, Paragraph) else _p(v, "cellb")])
        if len(row) == 4:
            cells.append(row)
            row = []
    if row:
        row += [["", ""]] * (4 - len(row))
        cells.append(row)
    flat = [[c for pair in r for c in pair] for r in cells]
    w = width / 4
    t = Table(flat, colWidths=[w * 0.5, w * 0.5] * 4)
    t.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, RULE),
                ("BACKGROUND", (0, 0), (-1, -1), ZEBRA),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return t


def _decorate(title: str, subtitle: str):
    word = asset_path("logo_wordmark.png")
    mark = asset_path("logo_mark.png")

    def draw(canvas, doc) -> None:  # noqa: ANN001
        canvas.saveState()
        canvas.setFillColor(BAND)
        canvas.rect(0, PAGE_H - BAND_H, PAGE_W, BAND_H, stroke=0, fill=1)
        canvas.setStrokeColor(colors.HexColor("#3a3a43"))
        canvas.setLineWidth(0.6)
        canvas.line(0, PAGE_H - BAND_H, PAGE_W, PAGE_H - BAND_H)
        x = MARGIN
        if mark:
            h = BAND_H - 8 * mm
            canvas.drawImage(mark, x, PAGE_H - BAND_H + 4 * mm, width=h * 143 / 160, height=h, mask="auto")
            x += h * 143 / 160 + 5 * mm
        if word:
            h = 6.2 * mm
            canvas.drawImage(word, x, PAGE_H - BAND_H / 2 - h / 2, width=h * 421 / 90, height=h, mask="auto")
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - BAND_H / 2 + 1.5 * mm, title)
        canvas.setFillColor(SILVER)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - BAND_H / 2 - 3.5 * mm, subtitle)
        # footer
        canvas.setStrokeColor(RULE)
        canvas.line(MARGIN, 13 * mm, PAGE_W - MARGIN, 13 * mm)
        canvas.setFillColor(FAINT)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(
            MARGIN,
            9 * mm,
            f"{BRAND} Automations · {PRODUCT} · Decision support only - every code must be reviewed and approved by a certified coder.",
        )
        canvas.drawRightString(PAGE_W - MARGIN, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()

    return draw


def _build(story: list, title: str, prefix: str) -> str:
    out = Path(tempfile.mkdtemp(prefix="aaxis_")) / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.pdf"
    doc = SimpleDocTemplate(
        str(out),
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=BAND_H + 9 * mm,
        bottomMargin=18 * mm,
        title=title,
        author=f"{BRAND} Automations",
    )
    deco = _decorate(title, f"Generated {datetime.now():%Y-%m-%d %H:%M}")
    doc.build(story, onFirstPage=deco, onLaterPages=deco)
    return str(out)


def _issues_table(issues: list, width: float) -> Table | Paragraph:
    if not issues:
        return _colored("No denial risks found against the loaded reference data.", STATUS_COLOR["clean"])
    order = {"deny": 0, "review": 1, "info": 2}
    rows = []
    for i in sorted(issues, key=lambda x: (order.get(x.severity, 3), x.line or 0)):
        label = {"deny": "DENIAL RISK", "review": "REVIEW", "info": "NOTE"}.get(
            i.severity, i.severity.upper()
        )
        msg = (
            escape(i.message)
            + (f'<br/><font color="{MUTED.hexval()}">Fix: {escape(i.fix)}</font>' if i.fix else "")
            + (
                f'<br/><font color="{FAINT.hexval()}" size="7">Source: {escape(i.source)}</font>'
                if i.source
                else ""
            )
        )
        rows.append(
            [
                _colored(label, SEV_COLOR.get(i.severity, INK)),
                _p(i.line or "claim"),
                Paragraph(msg, S["cell"]),
                _p((i.carc or "") + (f" / {i.rarc}" if i.rarc else ""), "code"),
            ]
        )
    return _table(
        ["Severity", "Line", "Finding and fix", "CARC/RARC"],
        rows,
        [width * 0.14, width * 0.08, width * 0.63, width * 0.15],
    )


def _status_para(scrub) -> Paragraph:  # noqa: ANN001
    return _colored(STATUS_LABEL.get(scrub.status, scrub.status), STATUS_COLOR.get(scrub.status, INK))


# ------------------------------------------------------------------ public


def note_report(out: dict, meta: dict, note_text: str) -> str:
    W = PAGE_W - 2 * MARGIN
    sugs, scrub, claim = out["suggestions"], out["scrub"], out["claim"]
    story: list = [
        _p("Medical Coding Report", "h1"),
        _p(
            "Suggested codes with evidence, guideline citations, draft claim and pre-bill denial check.",
            "small",
        ),
        Spacer(1, 8),
        _kv(
            [
                ("Document", out["document_id"][:8]),
                ("Date of service", str(claim.date_of_service)),
                ("Setting", meta.get("encounter")),
                ("Claim type", claim.claim_type),
                (
                    "Patient",
                    f"{meta.get('sex') or 'n/a'} / {meta.get('age') if meta.get('age') is not None else 'n/a'}",
                ),
                ("Claim status", _status_para(scrub)),
                ("Denial risk", f"{scrub.risk_score}%"),
                ("Review route", "Coder review" if out["route"] == "mandatory" else "Standard"),
            ],
            W,
        ),
        _p("Suggested codes", "h2"),
    ]
    rows = [
        [
            _p(s["sequence"]),
            _p(s["code_system"]),
            _p(s["code"], "code"),
            Paragraph(
                escape(s["description"])
                + "".join(
                    f'<br/><font color="{SEV_COLOR["review"].hexval()}">! {escape(i)}</font>'
                    for i in s["issues"]
                ),
                S["cell"],
            ),
            _p(f"{float(s['confidence']) * 100:.1f}%"),
            _p(s["validation"]),
            _p("coder review" if s["route"] == "mandatory" else s["route"]),
        ]
        for s in sugs
    ]
    story.append(
        _table(
            ["#", "System", "Code", "Description", "Conf.", "Validation", "Route"],
            rows,
            [W * 0.04, W * 0.12, W * 0.1, W * 0.44, W * 0.08, W * 0.1, W * 0.12],
        )
        if rows
        else _p("No codes suggested.", "body")
    )

    story.append(_p("Evidence and guideline citations", "h2"))
    for s in sugs:
        block = [
            Paragraph(
                f'<font name="Courier-Bold">{escape(s["code"])}</font>  {escape(s["description"])}',
                S["cellb"],
            )
        ]
        for q in s["evidence"][:2]:
            block.append(
                Paragraph(f'<font color="{MUTED.hexval()}">Evidence: “{escape(q[:400])}”</font>', S["small"])
            )
        for g in s["guidelines"][:2]:
            block.append(
                Paragraph(
                    f'<font color="{MUTED.hexval()}"><b>{escape(g["citation"])}</b> - {escape(g["title"])}: {escape(g["snippet"][:420])}</font>',
                    S["small"],
                )
            )
        block.append(Spacer(1, 5))
        story.append(KeepTogether(block))

    story.append(_p("Draft claim", "h2"))
    story.append(
        _p(
            "Diagnoses (pointer order): "
            + (", ".join(f"{i}. {c}" for i, c in enumerate(claim.diagnoses, 1)) or "none"),
            "body",
        )
    )
    if claim.lines:
        story.append(Spacer(1, 4))
        story.append(
            _table(
                ["Line", "System", "Code", "Units", "Description"],
                [
                    [_p(n), _p(x.system), _p(x.code, "code"), _p(f"{x.units:g}"), _p(x.description or "")]
                    for n, x in enumerate(claim.lines, 1)
                ],
                [W * 0.07, W * 0.14, W * 0.12, W * 0.08, W * 0.59],
            )
        )
    story.append(_p("Pre-bill claim check", "h2"))
    story.append(_issues_table(scrub.issues, W))
    if scrub.not_checked:
        story.append(Spacer(1, 4))
        story.append(_p("Not checked (reference data not loaded): " + "; ".join(scrub.not_checked), "small"))

    proc_nc = [n for n in out.get("not_coded", []) if n.get("reason", "").startswith("procedure")]
    excluded = [x for x in out["entities"] if x["status"] != "affirmed"]
    if proc_nc or excluded:
        story.append(_p("Not coded", "h2"))
        for n in proc_nc:
            story.append(_p(f"{n['entity']}: {n['reason']}", "small"))
        if excluded:
            story.append(_p("; ".join(f"{x['text']} ({x['status']})" for x in excluded[:40]), "small"))

    if note_text:
        story.append(_p("Source note", "h2"))
        story.append(Paragraph(escape(note_text[:12000]).replace("\n", "<br/>"), S["mono"]))
    return _build(story, "Medical Coding Report", "coding_report")


def claim_report(res, claim_in: dict) -> str:  # noqa: ANN001
    W = PAGE_W - 2 * MARGIN
    story: list = [
        _p("Pre-bill Claim Check", "h1"),
        _p("Denial risks with typical CARC/RARC codes, fixes and rule sources.", "small"),
        Spacer(1, 8),
        _kv(
            [
                ("Date of service", claim_in.get("date_of_service")),
                ("Claim type", claim_in.get("claim_type")),
                ("Setting", claim_in.get("encounter_type")),
                (
                    "Patient",
                    f"{claim_in.get('patient_sex') or 'n/a'} / {claim_in.get('patient_age') if claim_in.get('patient_age') is not None else 'n/a'}",
                ),
                ("Status", _status_para(res)),
                ("Denial risk", f"{res.risk_score}%"),
                ("At risk", f"${res.dollars_at_risk:,.2f}" if res.dollars_at_risk else "$0.00"),
                ("Findings", len(res.issues)),
            ],
            W,
        ),
        _p("Claim as submitted", "h2"),
        _p(
            "Diagnoses: "
            + (", ".join(f"{i}. {c}" for i, c in enumerate(claim_in.get("diagnoses", []), 1)) or "none"),
            "body",
        ),
        Spacer(1, 4),
    ]
    lines = claim_in.get("lines", [])
    if lines:
        story.append(
            _table(
                ["Line", "Code", "Units", "Modifiers", "Dx pointers", "Charge"],
                [
                    [
                        _p(n),
                        _p(x["code"], "code"),
                        _p(f"{x['units']:g}"),
                        _p(" ".join(x.get("modifiers") or []) or "-"),
                        _p(", ".join(str(p) for p in x.get("dx_pointers") or []) or "all"),
                        _p(f"${x['charge']:,.2f}" if x.get("charge") is not None else "-"),
                    ]
                    for n, x in enumerate(lines, 1)
                ],
                [W * 0.08, W * 0.16, W * 0.1, W * 0.2, W * 0.2, W * 0.26],
            )
        )
    story.append(_p("Findings", "h2"))
    story.append(_issues_table(res.issues, W))
    if res.not_checked:
        story.append(Spacer(1, 4))
        story.append(_p("Not checked (reference data not loaded): " + "; ".join(res.not_checked), "small"))
    if res.references:
        story.append(Spacer(1, 6))
        story.append(
            _p("Reference data: " + "; ".join(f"{k} {v}" for k, v in sorted(res.references.items())), "small")
        )
    return _build(story, "Pre-bill Claim Check", "claim_check")


def lookup_report(data: dict) -> str:
    W = PAGE_W - 2 * MARGIN
    story: list = [
        _p("Code Reference Sheet", "h1"),
        _p(f"{data['system']} search: “{data['query']}”", "small"),
        Spacer(1, 8),
    ]
    top = data.get("top")
    if top:
        story.append(
            Paragraph(f'<font name="Courier-Bold" size="16">{escape(top["code"])}</font>', S["body"])
        )
        story.append(Spacer(1, 6))
        story.append(_p(top["description"], "cellb"))
        story.append(Spacer(1, 6))
        story.append(
            _table(["Field", "Value"], [[_p(k), _p(v)] for k, v in top["facts"]], [W * 0.25, W * 0.75])
        )
    story.append(_p("Matching codes", "h2"))
    story.append(
        _table(
            ["Code", "Description", "Billable"],
            [
                [_p(r["code"], "code"), _p(r["description"]), _p("yes" if r["billable"] else "no")]
                for r in data["results"]
            ],
            [W * 0.14, W * 0.74, W * 0.12],
        )
        if data["results"]
        else _p("No matching codes.", "body")
    )
    return _build(story, "Code Reference Sheet", "code_lookup")
