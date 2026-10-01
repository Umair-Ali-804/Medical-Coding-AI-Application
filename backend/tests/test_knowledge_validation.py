from __future__ import annotations

from app.extraction.types import Entity
from app.knowledge.codes import normalize_code, parse_code_refs, ref_matches
from app.validation.engine import Proposal, ValidationContext, ValidationEngine

NOTE = (
    "HPI: 60 year old male. Denies chest pain.\n"
    "Assessment:\n1. Type 2 diabetes mellitus without complications\n2. Osteoarthritis of left knee"
)


def ents() -> list[Entity]:
    return [
        Entity(
            "chest pain",
            "symptom",
            NOTE.index("chest pain"),
            NOTE.index("chest pain") + 10,
            "chest pain",
            "hpi",
            negated=True,
        ),
        Entity(
            "Type 2 diabetes mellitus without complications",
            "condition",
            NOTE.index("Type 2"),
            NOTE.index("Type 2") + 46,
            "type 2 diabetes mellitus",
            "assessment",
        ),
        Entity(
            "Osteoarthritis of left knee",
            "condition",
            NOTE.index("Osteo"),
            NOTE.index("Osteo") + 27,
            "osteoarthritis",
            "assessment",
            laterality="left",
        ),
    ]


def validate(kb, proposals, sex="M", age=60):
    ctx = ValidationContext(
        text=NOTE, entities=ents(), snapshots={"ICD-10-CM": kb}, patient_sex=sex, patient_age=age
    )
    return {v.code: v for v in ValidationEngine().validate(proposals, ctx)}


def P(code, evidence=None, entity=""):  # noqa: N802
    return Proposal(
        code=code, system="ICD-10-CM", evidence=evidence or [], entity_text=entity, llm_confidence=0.9
    )


def test_code_helpers():
    assert normalize_code("e119") == "E11.9"
    assert normalize_code(" s93.401a ") == "S93.401A"
    refs = parse_code_refs("type 1 diabetes mellitus (E10.-)")
    assert ref_matches("E10.9", refs[0]) and not ref_matches("E11.9", refs[0])
    rng = parse_code_refs("hypertensive disease complicating pregnancy (O10-O11, O13-O16)")
    assert any(ref_matches("O10.02", r) for r in rng) and not any(ref_matches("O12.0", r) for r in rng)


def test_kb_parse_seventh_character_and_billable(kb):
    assert kb.get("S93.401A").billable
    assert kb.get("T78.40XA").billable  # placeholder X expansion
    assert not kb.get("E11").billable
    assert kb.get("E11.9").description == "Type 2 diabetes mellitus without complications"
    assert any("E10" in n for _o, n in kb.inherited("E11.9", "excludes1"))


def test_valid_code_passes(kb):
    r = validate(
        kb, [P("E11.9", ["Type 2 diabetes mellitus without complications"], "Type 2 diabetes mellitus")]
    )
    assert r["E11.9"].status == "passed", r["E11.9"].issues
    assert r["E11.9"].best_evidence_score == 1.0


def test_nonexistent_and_nonbillable_rejected(kb):
    r = validate(kb, [P("E11.99", ["Type 2 diabetes"]), P("E11", ["Type 2 diabetes"])])
    assert r["E11.99"].status == "rejected"
    assert r["E11"].status == "rejected"
    assert any(i.rule == "billable" and i.related_codes for i in r["E11"].issues)


def test_hallucinated_evidence_flagged(kb):
    r = validate(kb, [P("E11.9", ["Patient has severe diabetic retinopathy with macular edema"], "diabetes")])
    assert any(i.rule == "evidence_not_found" for i in r["E11.9"].issues)
    assert r["E11.9"].status == "flagged"


def test_negated_condition_flagged(kb):
    r = validate(kb, [P("R07.9", ["Denies chest pain"], "chest pain")])
    assert any(i.rule == "negation_contradiction" for i in r["R07.9"].issues)


def test_excludes1_and_unspecified_with_specific(kb):
    ev = ["Type 2 diabetes mellitus without complications"]
    r = validate(
        kb, [P("E11.9", ev, "Type 2 diabetes"), P("E10.9", ev, "Type 2 diabetes"), P("E11.65", ev, "Type 2")]
    )
    assert any(i.rule == "excludes1" for i in r["E11.9"].issues)
    assert any(i.rule == "unspecified_with_specific" for i in r["E11.9"].issues)


def test_laterality_mismatch(kb):
    r = validate(kb, [P("M17.11", ["Osteoarthritis of left knee"], "Osteoarthritis of left knee")])
    assert any(i.rule == "laterality_mismatch" for i in r["M17.11"].issues)
    ok = validate(kb, [P("M17.12", ["Osteoarthritis of left knee"], "Osteoarthritis of left knee")])
    assert not any(i.rule.startswith("laterality") for i in ok["M17.12"].issues)


def test_sex_edit_and_duplicate(kb):
    r = ValidationEngine().validate(
        [P("O24.410", ["Type 2 diabetes mellitus"]), P("O24.410", ["Type 2 diabetes mellitus"])],
        ValidationContext(
            text=NOTE, entities=ents(), snapshots={"ICD-10-CM": kb}, patient_sex="M", patient_age=60
        ),
    )
    assert any(i.rule == "sex_edit" for i in r[0].issues)
    assert any(i.rule == "duplicate" for i in r[1].issues) and r[1].status == "rejected"


def test_unloaded_code_system_rejected(kb):
    ctx = ValidationContext(text=NOTE, entities=[], snapshots={"ICD-10-CM": kb})
    v = ValidationEngine().validate([Proposal(code="99213", system="CPT", evidence=["x"])], ctx)[0]
    assert v.status == "rejected" and v.issues[0].rule == "code_system_unavailable"
