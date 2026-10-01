"""AAXIS AI - Medical Coding Intelligence workbench (Gradio + custom HTML/CSS) for Colab / pilots.

    python -m app.demo.gradio_app [--share] [--port 7860]

Tabs: Code a note (suggestions, evidence, guideline citations, draft claim, denial check, PDF report);
Claim scrubber (PDF); Code lookup (PDF); Official guidelines; Reference data.
The coding logic is unchanged: this module only calls the existing services and renders the results.
"""

from __future__ import annotations

import argparse
import inspect
from datetime import date
from pathlib import Path

from app.core.config import get_settings
from app.db.session import session_scope
from app.demo import ui_render as R
from app.demo.ui_theme import BRAND, CSS, HEAD, PRODUCT, asset_path, theme

ROOT = Path(__file__).resolve().parents[3]
SAMPLE_DIRS = [ROOT / "data" / "sample_notes", ROOT / "data" / "raw"]

EXAMPLE_CLAIM_LINES = [
    ["99213", 1, "", "1", 120],
    ["20610", 3, "RT LT", "1", 150],
    ["J1100", 150, "", "1", 2],
    ["A4397", 1, "", "5", 10],
]


def _samples() -> dict[str, Path]:
    out = {}
    for d in SAMPLE_DIRS:
        if d.exists():
            for p in sorted(d.glob("*.txt")):
                out[f"{d.name}/{p.name}"] = p
    return out


def _age(v: object) -> int | None:
    s = str(v or "").strip()
    return int(float(s)) if s.replace(".", "", 1).isdigit() and float(s) > 0 else None


def _download(path: str | None):
    import gradio as gr

    return gr.DownloadButton(value=path, visible=bool(path))


# ------------------------------------------------------------------ handlers (call existing services)


def load_sample(name: str) -> str:
    p = _samples().get(name)
    return p.read_text() if p else ""


def run_note(text: str, encounter: str, sex: str, age: str, dos: str):
    from app.demo.pdf_report import note_report
    from app.demo.service import code_note

    if not text or len(text.strip()) < 20:
        return R.empty_html(
            "Paste a clinical note", "Load a sample or paste at least a few sentences, then run the analysis."
        ), _download(None)
    meta = {"encounter": encounter, "sex": sex or None, "age": _age(age)}
    try:
        out = code_note(
            text,
            encounter_type=encounter,
            patient_sex=meta["sex"],
            patient_age=meta["age"],
            dos=(dos or "").strip() or None,
        )
    except Exception as exc:  # noqa: BLE001 - show the reason in the UI instead of a broken page
        return R.error_html(f"{type(exc).__name__}: {exc}"), _download(None)
    html = R.note_html(out, meta)
    try:
        pdf = note_report(out, meta, text)
    except Exception:  # noqa: BLE001 - the screen result must not depend on the PDF
        pdf = None
    return html, _download(pdf)


def _claim_input(dx: str, lines, claim_type: str, encounter: str, sex: str, age: str, dos: str) -> dict:
    rows = lines.values.tolist() if hasattr(lines, "values") else (lines or [])
    claim_lines = []
    for r in rows:
        if not r or not str(r[0]).strip():
            continue
        code, units, mods, ptrs, charge = (list(r) + [None] * 5)[:5]
        claim_lines.append(
            {
                "code": str(code).strip(),
                "units": float(units or 1),
                "modifiers": str(mods or "").replace(",", " ").split(),
                "dx_pointers": [
                    int(x) for x in str(ptrs or "").replace(",", " ").split() if x.strip().isdigit()
                ],
                "charge": float(charge) if charge not in (None, "") else None,
            }
        )
    return {
        "diagnoses": [d.strip() for d in (dx or "").replace(";", ",").split(",") if d.strip()],
        "lines": claim_lines,
        "claim_type": claim_type,
        "encounter_type": encounter,
        "patient_sex": sex or None,
        "patient_age": _age(age),
        "date_of_service": (dos or "").strip() or str(date.today()),
    }


def run_claim(dx: str, lines, claim_type: str, encounter: str, sex: str, age: str, dos: str):
    from app.demo.pdf_report import claim_report
    from app.demo.service import scrub_claim

    try:
        claim_in = _claim_input(dx, lines, claim_type, encounter, sex, age, dos)
        res = scrub_claim(claim_in)
    except Exception as exc:  # noqa: BLE001
        return R.error_html(f"{type(exc).__name__}: {exc}"), _download(None)
    try:
        pdf = claim_report(res, claim_in)
    except Exception:  # noqa: BLE001
        pdf = None
    return R.claim_html(res, claim_in), _download(pdf)


def lookup_data(query: str, system: str) -> dict:
    from app.knowledge.edits import get_edits
    from app.knowledge.repository import get_snapshot
    from app.rag.lexical import get_bm25

    data = {"query": query, "system": system, "results": [], "top": None}
    if not query or len(query.strip()) < 2:
        data["error"] = "Type a code or at least two letters."
        return data
    with session_scope() as db:
        snap = get_snapshot(db, system)
        if snap is None:
            data["error"] = f"{system} is not loaded."
            return data
        hits = []
        direct = snap.get(query.strip())
        if direct:
            hits.append(direct)
        for code, _score in get_bm25(snap).search(query, 25):
            info = snap.codes[code]
            if not direct or info.code != direct.code:
                hits.append(info)
        data["results"] = [
            {"code": h.code, "description": h.description, "billable": h.billable} for h in hits[:25]
        ]
        top = direct or (hits[0] if hits else None)
        if top:
            edits = get_edits(db)
            facts: list[tuple[str, str]] = [("Code set", f"{system} {top.version}")]
            a = top.attributes or {}
            labels = {
                "coverage_description": "Medicare coverage",
                "betos": "BETOS",
                "added": "Added",
                "terminated": "Terminated",
                "type_of_service": "Type of service",
                "category": "Category",
                "specialty": "Specialty",
                "section": "PCS section",
                "root_operation": "Root operation",
                "short_description": "Short description",
            }
            for k, label in labels.items():
                if a.get(k):
                    facts.append((label, str(a[k])))
            if top.chapter_description and system == "ICD-10-CM":
                facts.append(("Chapter", top.chapter_description))
            for name, table in edits.mue.items():
                if top.code in table:
                    v, _mai, rat = table[top.code]
                    facts.append((name, f"max {v:g} units per day ({rat})"))
            pn = a.get("processing_note")
            if pn and pn in edits.notes:
                facts.append((f"Processing note {pn}", edits.notes[pn]))
            if system == "ICD-10-CM":
                for label, attr in (
                    ("Excludes1", "excludes1"),
                    ("Excludes2", "excludes2"),
                    ("Use additional code", "use_additional_code"),
                    ("Code first", "code_first"),
                ):
                    notes = snap.inherited(top.code, attr)
                    if notes:
                        facts.append((label, "; ".join(f"{n} ({o})" for o, n in notes[:6])))
            data["top"] = {
                "code": top.code,
                "description": top.description,
                "billable": top.billable,
                "facts": facts,
            }
    return data


def lookup(query: str, system: str):
    from app.demo.pdf_report import lookup_report

    try:
        data = lookup_data(query, system)
    except Exception as exc:  # noqa: BLE001
        return R.error_html(f"{type(exc).__name__}: {exc}"), _download(None)
    pdf = None
    if not data.get("error") and data["results"]:
        try:
            pdf = lookup_report(data)
        except Exception:  # noqa: BLE001
            pdf = None
    return R.lookup_html(data), _download(pdf)


def guideline_search(query: str, system: str):
    from app.knowledge import guidelines

    if not query:
        return R.empty_html(
            "Ask the guidelines", "Type a topic, e.g. 'uncertain diagnosis outpatient' or 'sepsis'."
        )
    try:
        with session_scope() as db:
            hits = guidelines.search(db, query, None if system == "Both" else system, k=6)
    except Exception as exc:  # noqa: BLE001
        return R.error_html(f"{type(exc).__name__}: {exc}")
    return R.guidelines_html(hits)


def reference_data(dos: str) -> dict:
    from app.knowledge.edits import get_edits
    from app.knowledge.loader_service import active_versions, freshness_warnings

    on = date.fromisoformat(dos.strip()) if dos and dos.strip() else date.today()
    with session_scope() as db:
        sets = []
        for v in active_versions(db):
            ok = (not v.effective_from or on >= v.effective_from) and (
                not v.effective_to or on <= v.effective_to
            )
            sets.append(
                {
                    "set": v.code_system,
                    "version": v.version,
                    "rows": v.code_count,
                    "from": str(v.effective_from or "-"),
                    "to": str(v.effective_to or "-"),
                    "valid": ok,
                }
            )
        warns = freshness_warnings(db, on)
        have = set(get_edits(db).sets)
    missing = [
        ("NCCI-PTP", "NCCI procedure-to-procedure edits (bundling) - CMS NCCI PTP files"),
        ("COVERAGE", "LCD/NCD diagnosis-to-procedure coverage - CMS Medicare Coverage Database"),
        ("FEE", "Fee schedule (dollars at risk) - CMS Physician Fee Schedule / your contracts"),
        ("MUE-OPH", "MUE for outpatient hospital claims - CMS NCCI MUE"),
        ("MUE-DME", "MUE for DME supplier claims - CMS NCCI MUE"),
    ]
    s = get_settings()
    return {
        "dos": str(on),
        "sets": sets,
        "warnings": warns,
        "missing": [d for k, d in missing if k not in have],
        "llm": s.llm_provider,
        "embed": s.embedding_provider,
        "vectors": s.vector_store,
    }


def reference_status(dos: str) -> str:
    try:
        return R.reference_html(reference_data(dos))
    except Exception as exc:  # noqa: BLE001
        return R.error_html(f"{type(exc).__name__}: {exc}")


def _valid_to() -> str | None:
    try:
        from app.knowledge.loader_service import active_versions

        with session_scope() as db:
            ends = [v.effective_to for v in active_versions(db) if v.effective_to]
        return str(min(ends)) if ends else None
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ UI


def build():
    import gradio as gr

    samples = list(_samples())
    today = str(date.today())
    s = get_settings()
    blocks_kwargs = {"title": f"{BRAND} · {PRODUCT}"}
    styling = {"theme": theme(), "css": CSS, "head": HEAD}
    if "css" in inspect.signature(gr.Blocks.__init__).parameters:  # Gradio 4/5: styling on Blocks
        blocks_kwargs.update(styling)

    with gr.Blocks(**blocks_kwargs) as demo:
        gr.HTML(R.header_html(s.llm_provider, _valid_to()))
        with gr.Tabs(elem_classes="ax-tabs"):
            # ---------------------------------------------------- 1
            with gr.Tab("Code a Note"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=7, elem_classes="ax-panel"):
                        sample = gr.Dropdown(
                            samples, label="Sample notes", value=None, info="Synthetic examples"
                        )
                        note = gr.Textbox(
                            lines=15,
                            max_lines=40,
                            label="Clinical note",
                            placeholder="Paste a synthetic or de-identified clinical note…",
                            elem_classes="ax-note",
                        )
                    with gr.Column(scale=3, elem_classes="ax-panel"):
                        enc = gr.Radio(["outpatient", "inpatient"], value="outpatient", label="Encounter")
                        with gr.Row():
                            sex = gr.Dropdown(["", "M", "F"], value="", label="Sex")
                            age = gr.Textbox(label="Age", placeholder="e.g. 67")
                        dos = gr.Textbox(label="Date of service", value=today, info="YYYY-MM-DD")
                        go = gr.Button("Analyze note", variant="primary", elem_classes="ax-primary")
                        note_pdf = gr.DownloadButton(
                            "Download PDF report",
                            visible=False,
                            variant="secondary",
                            elem_classes="ax-download",
                        )
                        gr.HTML(
                            '<div class="ax-hint">Codes are suggestions. A certified coder approves every code.</div>'
                        )
                note_out = gr.HTML(
                    R.empty_html(
                        "Ready when you are",
                        "Choose a sample note or paste your own, then press Analyze note.",
                    )
                )
                sample.change(load_sample, sample, note)
                go.click(run_note, [note, enc, sex, age, dos], [note_out, note_pdf])

            # ---------------------------------------------------- 2
            with gr.Tab("Claim Scrubber"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=7, elem_classes="ax-panel"):
                        dx = gr.Textbox(
                            label="Diagnoses (ICD-10-CM, comma separated, first = primary)",
                            value="W19.XXXA, E11, M17.11, O24.419",
                        )
                        lines = gr.Dataframe(
                            headers=["code", "units", "modifiers", "dx pointers", "charge"],
                            value=EXAMPLE_CLAIM_LINES,
                            datatype=["str", "number", "str", "str", "number"],
                            interactive=True,
                            label="Service lines",
                        )
                    with gr.Column(scale=3, elem_classes="ax-panel"):
                        ctype = gr.Radio(
                            ["professional", "institutional"], value="professional", label="Claim type"
                        )
                        cenc = gr.Radio(["outpatient", "inpatient"], value="outpatient", label="Encounter")
                        with gr.Row():
                            csex = gr.Dropdown(["", "M", "F"], value="M", label="Sex")
                            cage = gr.Textbox(value="67", label="Age")
                        cdos = gr.Textbox(value=today, label="Date of service", info="YYYY-MM-DD")
                        cbtn = gr.Button("Check claim", variant="primary", elem_classes="ax-primary")
                        claim_pdf = gr.DownloadButton(
                            "Download PDF report",
                            visible=False,
                            variant="secondary",
                            elem_classes="ax-download",
                        )
                claim_out = gr.HTML(
                    R.empty_html(
                        "Pre-bill denial check",
                        "The example claim contains common denial causes. Press Check claim.",
                    )
                )
                cbtn.click(run_claim, [dx, lines, ctype, cenc, csex, cage, cdos], [claim_out, claim_pdf])

            # ---------------------------------------------------- 3
            with gr.Tab("Code Lookup"):
                with gr.Row(elem_classes="ax-panel"):
                    q = gr.Textbox(label="Code or words", value="dexamethasone injection", scale=5)
                    system = gr.Dropdown(
                        ["ICD-10-CM", "ICD-10-PCS", "HCPCS", "CPT"], value="HCPCS", label="Code set", scale=2
                    )
                    lb = gr.Button("Search", variant="primary", elem_classes="ax-primary", scale=1)
                lookup_pdf = gr.DownloadButton(
                    "Download PDF code sheet",
                    visible=False,
                    variant="secondary",
                    elem_classes=["ax-download", "ax-lookup-dl"],
                )
                lookup_out = gr.HTML(
                    R.empty_html(
                        "Code lookup", "Search ICD-10-CM, ICD-10-PCS, HCPCS or CPT by code or words."
                    )
                )
                lb.click(lookup, [q, system], [lookup_out, lookup_pdf])
                q.submit(lookup, [q, system], [lookup_out, lookup_pdf])

            # ---------------------------------------------------- 4
            with gr.Tab("Guidelines"):
                with gr.Row(elem_classes="ax-panel"):
                    gq = gr.Textbox(
                        label="Question or topic", value="uncertain diagnosis outpatient", scale=5
                    )
                    gs = gr.Radio(
                        ["Both", "ICD-10-CM", "ICD-10-PCS"], value="Both", label="Guidelines", scale=2
                    )
                    gb = gr.Button("Search", variant="primary", elem_classes="ax-primary", scale=1)
                g_out = gr.HTML(
                    R.empty_html(
                        "Official Coding Guidelines",
                        "Search the ICD-10-CM and ICD-10-PCS Official Guidelines.",
                    )
                )
                gb.click(guideline_search, [gq, gs], g_out)
                gq.submit(guideline_search, [gq, gs], g_out)

            # ---------------------------------------------------- 5
            with gr.Tab("Reference Data"):
                with gr.Row(elem_classes="ax-panel"):
                    rdos = gr.Textbox(
                        value=today, label="Check validity for date of service", info="YYYY-MM-DD", scale=5
                    )
                    rb = gr.Button("Refresh", variant="primary", elem_classes="ax-primary", scale=1)
                r_out = gr.HTML()
                rb.click(reference_status, rdos, r_out)
                demo.load(reference_status, rdos, r_out)
        gr.HTML(R.footer_html())
    demo._ax_styling = styling  # noqa: SLF001 - handed to launch() on Gradio 6
    return demo


def launch_kwargs(demo) -> dict:  # noqa: ANN001
    import gradio as gr

    params = inspect.signature(gr.Blocks.launch).parameters
    kw: dict = {}
    for k, v in getattr(demo, "_ax_styling", {}).items():
        if k in params:
            kw[k] = v
    if "favicon_path" in params and asset_path("favicon.png"):
        kw["favicon_path"] = asset_path("favicon.png")
    if "footer_links" in params:
        kw["footer_links"] = []
    return kw


def warm_up() -> None:
    """Load code sets, lexical indexes, guideline index and vectors once, so the first click is fast."""
    from app.knowledge import guidelines
    from app.knowledge.edits import get_edits
    from app.knowledge.repository import get_snapshot
    from app.rag.embeddings import get_embedder
    from app.rag.lexical import get_bm25
    from app.rag.retriever import HybridRetriever

    with session_scope() as db:
        for system in ("ICD-10-CM", "ICD-10-PCS", "HCPCS", "CPT"):
            snap = get_snapshot(db, system)
            if snap:
                get_bm25(snap)
                HybridRetriever(db, system).retrieve("warm up")
        get_edits(db)
        guidelines.get_index(db, "ICD-10-CM")
        guidelines.get_index(db, "ICD-10-PCS")
    get_embedder()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--share", action="store_true", help="public *.gradio.live link (Colab)")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--host", default="0.0.0.0")  # noqa: S104 - notebook/pilot use
    a = ap.parse_args()
    print("warming up (loading code sets and indexes)...", flush=True)
    warm_up()
    demo = build()
    demo.queue().launch(
        server_name=a.host, server_port=a.port, share=a.share, show_error=True, **launch_kwargs(demo)
    )


if __name__ == "__main__":
    main()
