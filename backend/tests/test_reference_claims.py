"""Reference-data loaders (CMS formats), claim scrubber, guidelines, procedure coding helpers.

Fixtures in tests/fixtures/reference are verbatim excerpts of the CMS files (HCPCS ANWEB, MUE,
processing notes, ICD-10-PCS); the CPT fixture uses test descriptions."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.knowledge import cms_loaders as L

FIX = Path(__file__).parent / "fixtures" / "reference"
REPO = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ parsers


def test_hcpcs_anweb_fixed_width():
    ls = L.parse_hcpcs_anweb(FIX / "HCPC2026_JUL_ANWEB_fixture.txt")
    assert ls.version == "2026Q3"
    assert (ls.effective_from, ls.effective_to) == (date(2026, 7, 1), date(2026, 9, 30))
    by = {r.code: r for r in ls.records}
    j = by["J1100"]
    assert j.description == "Injection, dexamethasone sodium phosphate, 1 mg"
    assert j.attributes["coverage_code"] == "D" and j.attributes["betos"] == "O1E"
    assert j.billable
    assert by["A4397"].attributes["terminated"] == "2021-12-31" and not by["A4397"].billable
    assert by["A4490"].attributes["coverage_code"] == "M"
    assert {"LT", "RT", "GA", "XS"} <= set(ls.extra["modifiers"])


def test_mue_header_window_and_values():
    ls = L.parse_mue(FIX / "MUE-PractitionerServices-fixture.txt")
    assert ls.kind == "MUE-PRAC"
    assert (ls.effective_from, ls.effective_to) == (date(2026, 7, 1), date(2026, 9, 30))
    by = {r.code: r for r in ls.records}
    assert by["20610"].value == 2 and by["J1100"].value == 120


def test_proc_notes_and_pcs():
    notes = {r.code: r.text for r in L.parse_proc_notes(FIX / "proc_notes_JUL2026.txt").records}
    assert "NOT VALID NOR PAYABLE BY MEDICARE" in notes["0088"]
    pcs = {r.code: r for r in L.parse_icd10pcs(FIX / "icd10pcs_codes_2026.txt").records}
    assert pcs["0FT44ZZ"].description.startswith("Resection of Gallbladder")
    assert pcs["0FT44ZZ"].attributes["section"] == "Medical and Surgical"


def test_cpt_csv_duplicates_ranges_and_license_gate():
    with pytest.raises(PermissionError):
        L.parse_cpt_csv(FIX / "cpt_fixture.csv", license_acknowledged=False)
    ls = L.parse_cpt_csv(FIX / "cpt_fixture.csv", license_acknowledged=True)
    by = {r.code: r for r in ls.records}
    assert ls.extra["duplicates"] == 1 and len(ls.records) == 7
    assert by["20610"].attributes["other_specialties"] == ["Sports Medicine"]
    rng = by["12001-12007"]
    assert not rng.billable and rng.attributes["range_start"] == "12001"


def test_detect_files():
    kinds = {f.kind for f in L.detect_files(FIX)}
    assert {"hcpcs", "mue", "proc_notes", "icd10pcs", "cpt"} <= kinds


def test_infer_units_for_drug_codes():
    from app.claims.scrubber import infer_units

    assert (
        infer_units("Injection, dexamethasone sodium phosphate, 1 mg", "injected with dexamethasone 8 mg")
        == 8
    )
    assert infer_units("Injection, ceftriaxone sodium, per 250 mg", "ceftriaxone 1000 mg IM") == 4
    assert infer_units("Arthrocentesis, major joint", "8 mg") == 1


# ------------------------------------------------------------------ loading + scrubbing


@pytest.fixture(scope="module")
def refs(kb):
    from app.db.session import session_scope
    from app.knowledge.loader_service import load_directory

    with session_scope() as db:
        rep = load_directory(db, FIX, cpt_license=True, out=None)
    assert {"HCPCS", "CPT", "ICD-10-PCS", "MUE-PRAC", "HCPCS-NOTES"} <= set(rep.loaded)
    return rep


def _scrub(**claim):
    from app.claims.scrubber import Claim, ClaimScrubber
    from app.db.session import session_scope

    claim.setdefault("date_of_service", "2026-09-15")
    with session_scope() as db:
        return ClaimScrubber(db).scrub(Claim.from_dict(claim))


def _rules(res) -> set[str]:
    return {i.rule for i in res.issues if i.severity in ("deny", "review")}


def test_clean_claim(refs):
    res = _scrub(patient_sex="F", patient_age=58, diagnoses=["E11.9", "I10"], lines=[{"code": "99214"}])
    assert res.status == "clean" and not _rules(res)


def test_diagnosis_rules(refs):
    res = _scrub(
        patient_sex="M",
        patient_age=67,
        diagnoses=["W19.XXXA", "E11", "N40.0", "O24.419"],
        lines=[{"code": "99213"}],
    )
    rules = _rules(res)
    assert {
        "dx_first_external_cause",
        "dx_not_billable",
        "dx_sex_edit",
        "dx_age_edit",
        "dx_excludes1",
    } <= rules
    carcs = {i.rule: i.carc for i in res.issues}
    assert carcs["dx_not_billable"] == "146" and carcs["dx_sex_edit"] == "7"


def test_manifestation_first_listed(refs):
    res = _scrub(patient_sex="M", patient_age=72, diagnoses=["F02.80", "G30.9"], lines=[{"code": "99214"}])
    assert "dx_first_manifestation" in _rules(res)


def test_line_rules_mue_modifiers_terminated_noncovered(refs):
    res = _scrub(
        patient_sex="M",
        patient_age=66,
        diagnoses=["M17.11"],
        lines=[
            {"code": "20610", "units": 3, "modifiers": ["RT", "LT"], "charge": 150},
            {"code": "J1100", "units": 200},
            {"code": "A4397"},
            {"code": "A4490"},
            {"code": "99213", "modifiers": ["Z9"]},
        ],
    )
    rules = _rules(res)
    assert {
        "mue_exceeded",
        "modifier_conflict",
        "code_terminated",
        "hcpcs_noncovered",
        "modifier_invalid",
    } <= rules
    assert "em_with_procedure_no_25" in rules  # 99213 + 20610 without modifier 25
    mue = [i for i in res.issues if i.rule == "mue_exceeded"]
    assert all(i.carc == "151" for i in mue)
    assert res.status == "likely_denial" and res.dollars_at_risk and res.dollars_at_risk >= 150


def test_modifier_25_and_vaccine_admin(refs):
    ok = _scrub(
        diagnoses=["M17.11", "E11.9"],
        lines=[{"code": "99213", "modifiers": ["25"]}, {"code": "20610", "modifiers": ["RT"]}],
    )
    assert "em_with_procedure_no_25" not in _rules(ok)
    vac = _scrub(diagnoses=["Z23"], lines=[{"code": "90715"}])
    assert "vaccine_without_admin" in _rules(vac)
    assert "vaccine_without_admin" not in _rules(
        _scrub(diagnoses=["Z23"], lines=[{"code": "90715"}, {"code": "90471"}])
    )


def test_pcs_setting_and_reference_window(refs):
    assert "pcs_on_outpatient" in _rules(_scrub(diagnoses=["K80.00"], lines=[{"code": "0FT44ZZ"}]))
    inpatient = _scrub(
        claim_type="institutional",
        encounter_type="inpatient",
        diagnoses=["K80.00"],
        lines=[{"code": "0FT44ZZ"}],
    )
    assert "pcs_on_outpatient" not in _rules(inpatient)
    late = _scrub(date_of_service="2026-10-05", diagnoses=["J06.9"], lines=[{"code": "99213"}])
    assert "reference_not_effective" in _rules(late)


def test_ptp_edits_when_loaded(refs, tmp_path):
    from app.db.session import session_scope
    from app.knowledge.loader_service import load_file

    f = tmp_path / "ccipra-v323r0-f1.txt"
    f.write_text(
        "Column 1\tColumn 2\t*=in existence prior to 1996\tEffective Date\tDeletion Date *=no data\t"
        "Modifier 0=not allowed 1=allowed 9=not applicable\tPTP Edit Rationale\n"
        "20610\t20550\t\t20090101\t*\t1\tStandards of medical / surgical practice\n"
        "93000\t93005\t\t19960101\t*\t0\tMisuse of column two code with column one code\n"
    )
    with session_scope() as db:
        load_file(db, f, "ptp", out=None)
    try:
        r1 = _scrub(diagnoses=["R07.9"], lines=[{"code": "93000"}, {"code": "93005"}])
        assert any(i.rule == "ncci_ptp" and i.carc == "236" and i.line == 2 for i in r1.issues)
        r2 = _scrub(diagnoses=["M79.1"], lines=[{"code": "20610"}, {"code": "20550", "modifiers": ["XS"]}])
        assert "ncci_ptp" not in _rules(r2)
        assert any(i.rule == "ncci_bypass_modifier" for i in r2.issues)
    finally:
        from sqlalchemy import delete

        from app.knowledge import edits
        from app.models import ClaimEdit, KnowledgeBaseVersion

        with session_scope() as db:
            db.execute(delete(ClaimEdit).where(ClaimEdit.edit_set == "NCCI-PTP"))
            db.execute(delete(KnowledgeBaseVersion).where(KnowledgeBaseVersion.code_system == "NCCI-PTP"))
        edits.clear_cache()


def test_claims_api(client, coder_headers, refs):
    r = client.post(
        "/api/v1/claims/scrub",
        headers=coder_headers,
        json={
            "date_of_service": "2026-09-15",
            "diagnoses": ["E11"],
            "lines": [{"code": "20610", "units": 5}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "likely_denial"
    assert {"dx_not_billable", "mue_exceeded"} <= {i["rule"] for i in body["issues"]}
    f = client.get("/api/v1/knowledge/freshness?dos=2026-10-05", headers=coder_headers)
    assert f.status_code == 200 and f.json()["warnings"]
    m = client.get("/api/v1/knowledge/edits/mue/20610", headers=coder_headers).json()
    assert m["mue"]["MUE-PRAC"]["units_per_day"] == 2


# ------------------------------------------------------------------ guidelines


def test_guideline_chunker_and_citation():
    from app.knowledge.guidelines import GuidelineIndex, chunk_icd10cm

    pages = [
        "Table of contents\n" + "\n".join(f"line {i}" for i in range(30)),
        "Section I. Conventions, general coding guidelines and chapter specific guidelines\n"
        "C. Chapter-Specific Coding Guidelines\n"
        "4. Chapter 4: Endocrine, Nutritional, and Metabolic Diseases (E00-E89)\n"
        "a. Diabetes mellitus\nAssign as many codes from categories E08 - E13 as needed. "
        "If the patient uses insulin assign Z79.4 Long term (current) use of insulin.\n"
        "Section IV. Diagnostic Coding and Reporting Guidelines for Outpatient Services\n"
        "H. Uncertain diagnosis\nDo not code diagnoses documented as probable, suspected, questionable.\n",
    ]
    chunks = chunk_icd10cm(pages)
    refs = {c.ref: c for c in chunks}
    assert "I.C.4.a" in refs and refs["I.C.4.a"].code_range == "E00-E89"
    assert "IV.H" in refs

    class Row:
        def __init__(self, c):
            self.code_system, self.version, self.ref, self.title = "ICD-10-CM", "2026", c.ref, c.title
            self.page, self.text, self.code_range = c.page, c.text, c.code_range

    idx = GuidelineIndex([Row(c) for c in chunks])
    hit = idx.search("type 2 diabetes insulin", k=1, code="Z79.4", encounter="outpatient")[0]
    assert hit.ref == "I.C.4.a" and "FY2026 I.C.4.a" in hit.citation()
    assert idx.search("probable pneumonia uncertain", k=1)[0].ref == "IV.H"


@pytest.mark.skipif(
    not (REPO / "data_sources" / "coding_guidlines.pdf").exists(), reason="guideline PDF not bundled"
)
def test_real_icd10cm_guidelines_pdf():
    from app.knowledge.guidelines import chunk_icd10cm, detect_system, extract_pages

    pages = extract_pages(REPO / "data_sources" / "coding_guidlines.pdf")
    assert detect_system(pages) == "ICD-10-CM"
    refs = {c.ref for c in chunk_icd10cm(pages)}
    assert {"I.C.4.a", "I.C.9.a", "IV.H", "II.H"} <= refs


# ------------------------------------------------------------------ procedures / vectors


def test_procedure_groups_performed_vs_planned():
    from app.coding.procedures import build_procedure_groups, expand_query
    from app.extraction.pipeline import ClinicalExtractor
    from app.ingestion.sections import detect_sections

    text = (
        "HPI: Patient is s/p appendectomy in 2010.\n"
        "Procedure: Right knee intra-articular injection. The joint was injected with dexamethasone 8 mg.\n"
        "Assessment: Primary osteoarthritis, right knee.\n"
        "Plan: Colonoscopy next month. MRI if no improvement.\n"
    )
    secs = detect_sections(text)
    ents = ClinicalExtractor().extract(text, secs).entities
    queries = [g.query.lower() for g in build_procedure_groups(text, secs, ents)]
    assert any("intra-articular injection" in q for q in queries)
    assert any("dexamethasone" in q for q in queries)
    assert not any("colonoscopy" in q or "appendectomy" in q or "mri" in q for q in queries)
    assert "resection gallbladder" in expand_query("laparoscopic cholecystectomy", "ICD-10-PCS")
    assert "percutaneous endoscopic" in expand_query("laparoscopic cholecystectomy", "ICD-10-PCS")


def test_disk_vector_store_roundtrip(tmp_path):
    from app.rag.vector_store import DiskStore

    s = DiskStore(tmp_path)
    s.ensure_collection("c", 3)
    s.upsert("c", ["a", "b"], [[1, 0, 0], [0, 1, 0]], [{"code": "A"}, {"code": "B"}])
    s.flush("c")
    s2 = DiskStore(tmp_path)  # new process view
    assert s2.count("c") == 2
    assert s2.search("c", [0.9, 0.1, 0], 1)[0].code == "A"
