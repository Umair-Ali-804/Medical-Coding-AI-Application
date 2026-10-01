from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from app.confidence.calibration import LabeledSuggestion, calibrate, choose_threshold
from app.confidence.scorer import Calibration, assess
from app.core.config import Settings
from app.core.errors import UpstreamError
from app.llm.client import OpenRouterClient, extract_json
from app.llm.schemas import CodingOutput, strict_json_schema


def settings(**kw) -> Settings:
    return Settings(
        llm_provider="openrouter", openrouter_api_key=SecretStr("sk-test"), llm_max_retries=3, **kw
    )


def chat(content: str, cost: float | None = 0.0012) -> dict:
    usage = {"prompt_tokens": 1000, "completion_tokens": 200}
    if cost is not None:
        usage["cost"] = cost
    return {
        "model": "test/model",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": usage,
    }


GOOD = json.dumps(
    {
        "codes": [
            {
                "code": "e11.9",
                "code_system": "ICD-10-CM",
                "description": "T2DM",
                "entity": "diabetes",
                "evidence": ["Type 2 diabetes"],
                "rationale": "documented",
                "confidence": 0.93,
                "from_candidates": True,
            }
        ],
        "not_coded": [],
    }
)


def test_structured_output_parsed_and_costed(monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.update(body)
        assert req.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(200, json=chat(f"```json\n{GOOD}\n```"))

    c = OpenRouterClient(settings(), transport=httpx.MockTransport(handler))
    res = c.complete_structured([{"role": "user", "content": "x"}], CodingOutput, schema_name="coding_result")
    assert res.parsed.codes[0].code == "E11.9"
    assert res.input_tokens == 1000 and res.cost_usd == pytest.approx(0.0012)
    assert seen["response_format"]["type"] == "json_schema"
    assert seen["provider"]["data_collection"] == "deny"
    assert seen["temperature"] == 0.0


def test_repair_round_on_invalid_output():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            200, json=chat('{"codes": [{"code": "E11.9"}]}' if len(calls) == 1 else GOOD, cost=None)
        )

    c = OpenRouterClient(settings(), transport=httpx.MockTransport(handler))
    res = c.complete_structured([{"role": "user", "content": "x"}], CodingOutput, schema_name="coding_result")
    assert res.repaired and len(calls) == 2
    # no provider cost -> computed from configured prices (2 calls x (1000 in, 200 out))
    assert res.cost_usd == pytest.approx(2 * (1000 / 1e6 * 3.0 + 200 / 1e6 * 15.0))


def test_retries_then_succeeds_on_429(monkeypatch):
    import tenacity

    monkeypatch.setattr(tenacity.nap, "sleep", lambda s: None)
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return (
            httpx.Response(429, json={"error": "rate"})
            if len(calls) < 3
            else httpx.Response(200, json=chat(GOOD))
        )

    c = OpenRouterClient(settings(), transport=httpx.MockTransport(handler))
    assert c.complete_structured(
        [{"role": "user", "content": "x"}], CodingOutput, schema_name="c"
    ).parsed.codes
    assert len(calls) == 3


def test_non_retryable_error_raises():
    c = OpenRouterClient(
        settings(), transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "bad key"}))
    )
    with pytest.raises(UpstreamError):
        c.complete_structured([{"role": "user", "content": "x"}], CodingOutput, schema_name="c")


def test_strict_schema_shape():
    schema = strict_json_schema(CodingOutput, "coding_result")
    assert schema["strict"] is True
    code_def = schema["schema"]["$defs"]["LLMCode"]
    assert code_def["additionalProperties"] is False
    assert set(code_def["required"]) == set(code_def["properties"])


def test_extract_json_variants():
    assert json.loads(extract_json('Here you go: {"a": 1} thanks'))["a"] == 1
    assert json.loads(extract_json('```json\n{"a": 2}\n```'))["a"] == 2


# ------------------------------------------------------------------ confidence
def test_routing():
    cal = Calibration(threshold=0.8)
    strong = {
        "evidence": 1.0,
        "retrieval": 0.9,
        "validation": 1.0,
        "entity": 0.8,
        "agreement": None,
        "llm": 0.9,
    }
    weak = {
        "evidence": 0.4,
        "retrieval": 0.0,
        "validation": 1.0,
        "entity": 0.0,
        "agreement": None,
        "llm": 0.9,
    }
    assert assess(strong, "passed", cal).route == "standard"
    assert assess(weak, "passed", cal).route == "mandatory"
    assert assess(strong, "flagged", cal).route == "mandatory"
    assert assess(strong, "rejected", cal).route == "system_rejected"
    assert assess(strong, "passed", cal).score > assess(weak, "passed", cal).score


def test_threshold_selected_from_data():
    scores = [0.99, 0.97, 0.95, 0.9, 0.85, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]
    correct = [True, True, True, True, True, True, True, False, True, False, False, False]
    thr, info = choose_threshold(scores, correct, [True] * 12, target_precision=0.95, min_support=5)
    assert thr == 0.7 and info["achieved_precision"] == 1.0


def test_calibration_fits_weights():
    rows = []
    for i in range(60):
        good = i % 3 != 0
        rows.append(
            LabeledSuggestion(
                {
                    "evidence": 1.0 if good else 0.5,
                    "retrieval": 0.9 if good else 0.2,
                    "validation": 1.0,
                    "entity": 0.8 if good else 0.1,
                    "agreement": None,
                    "llm": 0.9,
                },
                True,
                good,
            )
        )
    cal = calibrate(rows, target_precision=0.95)
    assert cal.source.startswith("fitted")
    assert cal.metrics["achieved_precision"] >= 0.95
