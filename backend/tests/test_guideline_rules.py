"""Coder rules (ICD-10-CM Official Guidelines / Alphabetic Index conventions) on the real FY2026 tabular.

Each test feeds a short note plus the codes a model proposed, and checks what a certified coder would report.
Negative tests make sure a rule does NOT fire when the documentation does not support it.
"""

from __future__ import annotations

import pytest

from app.coding.guideline_rules import apply_guideline_rules
from app.ingestion.sections import detect_sections
from app.llm.schemas import LLMCode, NotCoded


def run(
    kb, text: str, codes: list[str], encounter: str = "outpatient", age: int | None = 60
) -> tuple[list[str], list]:
    proposals = [LLMCode(code=c, description="", entity="", evidence=[], confidence=0.8) for c in codes]
    not_coded: list[NotCoded] = []
    changes = apply_guideline_rules(
        proposals, not_coded, text, detect_sections(text), encounter, kb, patient_age=age
    )
    return sorted(p.code for p in proposals), changes


@pytest.mark.parametrize(
    "text,proposed,expected",
    [
        # I.C.9.a hypertension + heart failure -> I11.0 (+ type of HF)
        (
            "Assessment:\n1. Acute on chronic systolic heart failure\n2. Hypertension",
            ["I10", "I50.23"],
            ["I11.0", "I50.23"],
        ),
        # HTN + CKD -> I12.9 + stage; diabetes + CKD -> E11.22; metformin -> Z79.84
        (
            "Medications: metformin 500 mg BID.\nAssessment:\n1. Type 2 diabetes with CKD stage 3b\n2. Hypertension",
            ["E11.9", "I10"],
            ["E11.22", "I12.9", "N18.32", "Z79.84"],
        ),
        # HTN + HF + ESRD on dialysis -> I13.2 + N18.6 + Z99.2
        (
            "ESRD on hemodialysis. Chronic diastolic heart failure. Hypertension.",
            ["I10", "I50.32", "N18.6"],
            ["I13.2", "I50.32", "N18.6", "Z99.2"],
        ),
        # history of MI -> I25.2 not I21
        (
            "History of myocardial infarction in 2019. Coronary artery disease without angina.",
            ["I21.9", "I25.10"],
            ["I25.10", "I25.2"],
        ),
        # former smoker -> Z87.891 not Z72.0
        ("Former smoker, quit 2010. COPD stable.", ["J44.9", "Z72.0"], ["J44.9", "Z87.891"]),
        # current smoker -> nicotine dependence (Index: Smoker)
        ("He currently smokes 1 pack of cigarettes per day.", ["Z72.0"], ["F17.210"]),
        # injury 7th character: S (sequela) not documented -> A
        (
            "ED: closed fracture of left fifth metatarsal shaft, initial encounter.",
            ["S92.352S"],
            ["S92.352A"],
        ),
        # injury site not documented -> removed (wrong-site hallucination)
        ("Displaced Colles fracture of the right distal radius.", ["S32.301S"], []),
        # abortive-outcome code without ectopic/abortion documentation -> removed; normal pregnancy -> Z34 + Z3A
        (
            "Prenatal visit. G1P0 at 20 weeks gestation, routine prenatal care, uncomplicated pregnancy.",
            ["O00.90"],
            ["Z34.02", "Z3A.20"],
        ),
        # Alzheimer late onset with dementia -> G30.1 + F02.80
        (
            "Late onset Alzheimer's disease with dementia, without behavioral disturbance.",
            ["G30.9", "F03.90"],
            ["F02.80", "G30.1"],
        ),
        # influenza A confirmed with pneumonia -> J10.00, no separate J18.9
        (
            "PCR positive for influenza A. Chest x-ray shows pneumonia. Diagnosis: Influenza A with pneumonia.",
            ["J11.00", "J18.9"],
            ["J10.00"],
        ),
        # bacterial pneumonia without organism -> J18.9
        ("Discharge diagnosis: community-acquired pneumonia.", ["J15.9"], ["J18.9"]),
        # asthma exacerbation
        ("Moderate persistent asthma with acute exacerbation.", ["J45.40"], ["J45.41"]),
        # OA knee laterality from note
        ("Assessment: Primary osteoarthritis of the right knee.", ["M19.011"], ["M17.11"]),
        # lumbago + sciatica combination
        ("Low back pain with left-sided sciatica.", ["M54.50", "M54.32"], ["M54.42"]),
        # diabetes + insulin and oral -> both Z79.4 and Z79.84 (I.C.4.a.3); foot ulcer -> E11.621 + L97
        (
            "Type 2 diabetes on insulin glargine and metformin with a diabetic ulcer of the right heel, limited to "
            "breakdown of the skin.",
            ["E11.622"],
            ["E11.621", "L97.411", "Z79.4", "Z79.84"],
        ),
        # hypoglycemia in a diabetic -> E10.649 (not E16.2)
        (
            "Type 1 diabetes with two episodes of hypoglycemia, no loss of consciousness.",
            ["E10.9", "E16.2"],
            ["E10.649"],
        ),
        # diabetic complication code without documented complication -> without complications
        ("Type 2 diabetes mellitus, A1c 6.8%. Monofilament intact.", ["E11.40"], ["E11.9"]),
        # sepsis organism not documented -> A41.9
        (
            "Sepsis due to urinary tract infection. Urine culture mixed flora.",
            ["A41.2", "N39.0"],
            ["A41.9", "N39.0"],
        ),
        # integral symptoms
        ("Burning on urination. Assessment: Urinary tract infection.", ["N39.0", "R30.0"], ["N39.0"]),
        # coronary stent placed THIS admission is a procedure, not a status (no Z95.5)
        (
            "NSTEMI. Cardiac catheterization showed CAD and a drug-eluting stent was placed.",
            ["I21.4", "I21.9"],
            ["I21.4"],
        ),
        # coronary stent from a previous encounter -> Z95.5
        (
            "CAD without angina. Status post PCI with stent to the LAD in 2019.",
            ["I25.10"],
            ["I25.10", "Z95.5"],
        ),
        # laterality is read next to the joint word
        ("Assessment: Osteoarthritis of the left knee, right knee normal.", ["M19.90"], ["M17.12"]),
        ("Assessment: Bilateral primary osteoarthritis of knees.", ["M19.90"], ["M17.0"]),
        # encounter reasons only when they are the purpose of THIS visit
        ("ANNUAL PHYSICAL\n39 y/o for annual wellness examination.", [], ["Z00.00"]),
        ("Indication: Screening colonoscopy, average risk.", [], ["Z12.11"]),
        # hard-wrapped text: the negation spans the line break
        ("Elevated blood pressure reading without diagnosis of\nhypertension.", ["I10", "R03.0"], ["R03.0"]),
    ],
)
def test_rules_fire(kb, text, proposed, expected):
    codes, changes = run(kb, text, proposed, encounter="inpatient" if "Discharge" in text else "outpatient")
    assert codes == sorted(expected), [c.as_dict() for c in changes]


@pytest.mark.parametrize(
    "text,proposed",
    [
        # plain hypertension: no combination
        ("Essential hypertension, well controlled. Kidney function normal. No heart failure.", ["I10"]),
        # documentation states the kidney disease is unrelated to hypertension
        (
            "Hypertension. CKD stage 2, unrelated to hypertension, due to polycystic kidney disease.",
            ["I10", "N18.2"],
        ),
        # negated heart failure must not trigger I11.0
        ("Hypertension. Denies heart failure symptoms; no CHF.", ["I10"]),
        # acute MI this admission stays acute
        ("Acute NSTEMI with troponin rise. History of hypertension.", ["I21.4", "I10"]),
        # family history of stroke is not the patient's stroke
        ("Father had a stroke. Patient well.", ["Z82.3"]),
        # cancer under current treatment is not history
        ("Left breast cancer on anastrozole, no recurrence.", ["C50.912", "Z79.811"]),
        # outpatient uncertain: rules never upgrade 'rule out'
        ("Cough and fever, rule out pneumonia.", ["R05.9", "R50.9"]),
        # type 1 diabetes: Z79.4 is not auto-added (insulin inherent)
        ("Type 1 diabetes on insulin pump, well controlled.", ["E10.9"]),
        # planned / recommended services are not this encounter's reason
        ("Sick visit for sinusitis. Annual physical is due next month.", ["J01.90"]),
        ("Hypertension. Recommended screening colonoscopy next year.", ["I10"]),
        # heart failure ruled out; family member's CKD; CKD from another cause
        ("Hypertension. Heart failure was ruled out by echo.", ["I10"]),
        ("Hypertension. Mother has chronic kidney disease stage 4.", ["I10"]),
        ("Type 2 diabetes. CKD stage 3a due to IgA nephropathy.", ["E11.9", "N18.31"]),
        # pulmonary hypertension is not essential hypertension
        ("Pulmonary hypertension with right heart failure.", ["I27.20", "I50.810"]),
        # influenza negative; insulin only as an inpatient sliding scale
        ("Negative for influenza A and B. Viral URI.", ["J06.9"]),
        (
            "Started on insulin sliding scale in hospital; type 2 diabetes otherwise diet controlled.",
            ["E11.9"],
        ),
    ],
)
def test_rules_do_not_fire(kb, text, proposed):
    codes, changes = run(kb, text, proposed)
    assert codes == sorted(proposed), [c.as_dict() for c in changes]


def test_unknown_codes_are_left_for_the_validator(kb):
    codes, _ = run(kb, "Type 2 diabetes mellitus without complications.", ["E11.9", "E11.99"])
    assert codes == ["E11.9", "E11.99"]  # E11.99 does not exist: validation rejects it, rules do not touch it


def test_every_change_is_cited_and_evidenced(kb):
    text = "Assessment:\n1. Hypertension\n2. Chronic kidney disease stage 4"
    proposals = [LLMCode(code="I10", description="", entity="", evidence=[], confidence=0.8)]
    not_coded: list[NotCoded] = []
    changes = apply_guideline_rules(proposals, not_coded, text, detect_sections(text), "outpatient", kb)
    assert {p.code for p in proposals} == {"I12.9", "N18.4"}
    for p in proposals:
        assert p.rationale.startswith("[guideline-rules")
        assert p.evidence and all(e in text for e in p.evidence)  # verbatim quotes
    assert all(c.guideline for c in changes)
    assert any("Replaced by I12.9" in n.reason for n in not_coded)
