"""The production LLM path end-to-end with a mocked OpenRouter: the validation engine must catch
hallucinated codes, fabricated evidence and negated diagnoses the LLM proposes."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from pydantic import SecretStr

from app.coding.models import OpenRouterCodingModel
from app.core.config import Settings, get_settings
from app.llm.client import OpenRouterClient
from app.services import pipeline
from app.services.principal import SYSTEM

NOTE = """Chief Complaint: Follow-up.
HPI: 58-year-old male with type 2 diabetes mellitus and hypertension. Denies chest pain.
Medications: Metformin 1000 mg twice daily.
Assessment:
1. Type 2 diabetes mellitus without complications
2. Essential hypertension"""

EXTRACTION = {
    "entities": [
        {
            "text": "type 2 diabetes mellitus",
            "category": "condition",
            "negated": False,
            "uncertain": False,
            "historical": False,
            "family": False,
            "laterality": None,
            "severity": None,
            "temporal": None,
        },
        {
            "text": "chest pain",
            "category": "symptom",
            "negated": False,  # LLM gets negation WRONG
            "uncertain": False,
            "historical": False,
            "family": False,
            "laterality": None,
            "severity": None,
            "temporal": None,
        },
        {
            "text": "Metformin",
            "category": "medication",
            "negated": False,
            "uncertain": False,
            "historical": False,
            "family": False,
            "laterality": None,
            "severity": None,
            "temporal": None,
        },
        {
            "text": "made-up finding not in note",
            "category": "finding",
            "negated": False,
            "uncertain": False,
            "historical": False,
            "family": False,
            "laterality": None,
            "severity": None,
            "temporal": None,
        },
    ]
}

CODING = {
    "codes": [
        {
            "code": "E11.9",
            "code_system": "ICD-10-CM",
            "description": "",
            "entity": "Type 2 diabetes mellitus",
            "evidence": ["Type 2 diabetes mellitus without complications"],
            "rationale": "assessment",
            "confidence": 0.95,
            "from_candidates": True,
        },
        {
            "code": "I10",
            "code_system": "ICD-10-CM",
            "description": "",
            "entity": "Essential hypertension",
            "evidence": ["Essential hypertension"],
            "rationale": "assessment",
            "confidence": 0.95,
            "from_candidates": True,
        },
        {
            "code": "Z79.84",
            "code_system": "ICD-10-CM",
            "description": "",
            "entity": "Metformin",
            "evidence": ["Metformin 1000 mg twice daily"],
            "rationale": "long-term oral hypoglycemic",
            "confidence": 0.9,
            "from_candidates": True,
        },
        {
            "code": "R07.9",
            "code_system": "ICD-10-CM",
            "description": "",
            "entity": "chest pain",
            "evidence": ["chest pain"],
            "rationale": "symptom",
            "confidence": 0.7,
            "from_candidates": True,
        },
        {
            "code": "E11.99",
            "code_system": "ICD-10-CM",
            "description": "invented",
            "entity": "diabetes",
            "evidence": ["diabetic retinopathy noted on exam"],
            "rationale": "hallucination",
            "confidence": 0.8,
            "from_candidates": False,
        },
    ],
    "not_coded": [],
}


@pytest.fixture()
def llm_model(monkeypatch):
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        name = body["response_format"]["json_schema"]["name"]
        calls.append(name)
        payload = EXTRACTION if name == "clinical_entities" else CODING
        return httpx.Response(
            200,
            json={
                "model": "anthropic/test",
                "usage": {"prompt_tokens": 2000, "completion_tokens": 300, "cost": 0.01},
                "choices": [{"message": {"content": json.dumps(payload)}}],
            },
        )

    s = Settings(llm_provider="openrouter", openrouter_api_key=SecretStr("sk-test"))
    model = OpenRouterCodingModel(s, client=OpenRouterClient(s, transport=httpx.MockTransport(handler)))
    live = get_settings()
    monkeypatch.setattr(live, "llm_provider", "openrouter")
    monkeypatch.setattr(pipeline, "get_coding_model", lambda: model)
    monkeypatch.setattr(pipeline, "get_secondary_model", lambda: None)
    return calls


def test_llm_pipeline_validation_catches_errors(kb, users, llm_model, db):
    from app.models import CodingSuggestion, ModelRun

    doc = pipeline.ingest_document(
        db, SYSTEM, data=NOTE.encode(), filename="n.txt", patient_sex="M", patient_age=58
    )
    db.commit()
    summary = pipeline.process_document(db, doc.id, SYSTEM)
    db.commit()
    assert llm_model == ["clinical_entities", "coding_result"]
    assert summary.suggestions == 5

    sugg = {s.code: s for s in db.query(CodingSuggestion).filter_by(document_id=doc.id)}
    # hallucinated code: does not exist -> system rejected
    assert sugg["E11.99"].validation_status == "rejected"
    assert sugg["E11.99"].review_route == "system_rejected"
    assert any(i["rule"] == "code_exists" for i in sugg["E11.99"].validation_issues)
    assert any(i["rule"] == "evidence_not_found" for i in sugg["E11.99"].validation_issues)
    # LLM missed the negation; deterministic ConText catches it -> conflict + contradiction -> mandatory review
    rules = {i["rule"] for i in sugg["R07.9"].validation_issues}
    assert {"negation_contradiction", "assertion_conflict"} & rules, rules
    assert sugg["R07.9"].review_route == "mandatory"
    # well-supported codes pass with verbatim evidence and higher confidence
    assert sugg["E11.9"].validation_status == "passed"
    assert sugg["E11.9"].evidence[0].match_score == 1.0
    assert sugg["E11.9"].confidence > sugg["R07.9"].confidence > sugg["E11.99"].confidence
    assert doc.review_route == "mandatory"

    runs = db.query(ModelRun).filter_by(document_id=doc.id).all()
    coding = next(r for r in runs if r.run_type == "coding")
    extraction = next(r for r in runs if r.run_type == "extraction")
    assert coding.model == "anthropic/test" and coding.cost_usd == pytest.approx(0.01)
    assert coding.prompt_version.startswith("code-rag") and extraction.prompt_version.startswith("extract")
    assert "not found verbatim" in json.dumps(extraction.config)  # dropped hallucinated entity is recorded


def test_webhook_signature(monkeypatch):
    from app.services import webhooks

    sent = {}

    def fake_post(url, content, headers, timeout):  # noqa: ANN001
        sent.update(url=url, body=content, headers=headers)
        return httpx.Response(200)

    s = get_settings()
    monkeypatch.setattr(s, "webhook_secret", SecretStr("whsec"))
    monkeypatch.setattr(webhooks.httpx, "post", fake_post)
    assert webhooks.send_event(
        "document.coded", {"document_id": str(uuid.uuid4())}, url="https://n8n.local/hook"
    )
    h = sent["headers"]
    assert h["X-MedCoding-Signature"] == webhooks.sign("whsec", h["X-MedCoding-Timestamp"], sent["body"])
    assert b"clinical" not in sent["body"]
