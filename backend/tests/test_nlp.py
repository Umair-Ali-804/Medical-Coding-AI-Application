from __future__ import annotations

import pytest

from app.extraction.context import ConTextAnalyzer, sentence_spans
from app.extraction.rule_extractor import RuleBasedExtractor
from app.extraction.types import Entity
from app.ingestion.cleaning import clean_text
from app.ingestion.sections import detect_sections


def analyze(text: str) -> dict[str, Entity]:
    text = clean_text(text)
    secs = detect_sections(text)
    ents = RuleBasedExtractor().extract(text, secs)
    ConTextAnalyzer().analyze(text, ents)
    return {e.text.lower(): e for e in ents}


@pytest.mark.parametrize(
    "sentence,term",
    [
        ("Patient denies chest pain.", "chest pain"),
        ("No evidence of pneumonia.", "pneumonia"),
        ("Negative for fever, chills, or cough.", "cough"),
        ("Pneumonia was ruled out.", "pneumonia"),
        ("He has no history of diabetes.", "diabetes"),
        ("Non-smoker. Lives alone.", "smoker"),
    ],
)
def test_negation(sentence, term):
    ents = analyze(sentence)
    assert ents[term].negated, ents[term]


@pytest.mark.parametrize(
    "sentence,term",
    [
        ("Possible pneumonia on imaging.", "pneumonia"),
        ("Chest pain, rule out myocardial infarction.", "myocardial infarction"),
        ("Findings consistent with COPD.", "copd"),
        ("Pneumonia vs. bronchitis.", "bronchitis"),
    ],
)
def test_uncertainty(sentence, term):
    assert analyze(sentence)[term].uncertain


def test_family_history_by_trigger_and_section():
    ents = analyze("HPI: Well.\nFamily History: Mother with breast cancer. Father has CAD.")
    assert ents["breast cancer"].family
    assert ents["cad"].family


def test_hypothetical_and_prevention():
    ents = analyze("Plan: Return if fever develops. On apixaban for stroke prevention.")
    assert ents["fever"].hypothetical
    assert ents["stroke"].hypothetical


def test_termination_limits_scope():
    ents = analyze("Denies fever but reports cough.")
    assert ents["fever"].negated
    assert not ents["cough"].negated


def test_negation_does_not_leak_across_sentences():
    ents = analyze("No chest pain. Patient has hypertension.")
    assert ents["chest pain"].negated
    assert not ents["hypertension"].negated


def test_abbreviation_does_not_split_sentence():
    text = "Pneumonia vs. bronchitis today."
    assert len(sentence_spans(text)) == 1


def test_short_abbreviations_require_uppercase():
    ents = analyze("Heating pad applied. History of PAD and HTN.")
    assert "pad" in ents and ents["pad"].text == "PAD"
    assert "htn" in ents


def test_sections_detected():
    text = "Chief Complaint: cough\nHPI: 3 days of cough.\nAssessment:\n1. Acute bronchitis\nPlan: rest"
    names = [s.name for s in detect_sections(text)]
    assert names == ["chief_complaint", "hpi", "assessment", "plan"]


def test_problem_list_compound_diagnosis():
    ents = analyze(
        "Assessment:\n1. Type 2 diabetes mellitus with hyperglycemia\n2. Hypertension and hyperlipidemia"
    )
    assert "type 2 diabetes mellitus with hyperglycemia" in ents
    assert "hyperglycemia" not in ents  # absorbed into the combination diagnosis
    assert "hypertension" in ents and "hyperlipidemia" in ents  # 'and' keeps conditions separate


def test_problem_list_leading_negation_kept_outside_span():
    ents = analyze("Assessment:\n1. No evidence of diabetic complications")
    assert ents["diabetic complications"].negated


def test_laterality_and_severity():
    ents = analyze("Assessment:\n1. Primary osteoarthritis of right knee, moderate")
    e = next(v for k, v in ents.items() if "osteoarthritis" in k)
    assert e.laterality == "right"
    assert e.severity == "moderate"
