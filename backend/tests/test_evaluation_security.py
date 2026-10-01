from __future__ import annotations

import time

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import create_access_token, decode_access_token, validate_password_strength
from app.evaluation.metrics import DocResult, aggregate, categorize_errors, prf
from app.evaluation.runner import evaluate, load_split
from tests.conftest import DATA_DIR


def test_prf():
    assert prf(8, 2, 2) == (0.8, 0.8, 0.8)
    assert prf(0, 0, 3) == (0.0, 0.0, 0.0)


def test_error_taxonomy():
    d = DocResult(
        "D",
        gold={"E11.9", "I10", "Z80.0"},
        predicted=["E11.65", "I10", "R07.9"],
        retrieved={"E11.9", "I10"},
        negated_predictions=["R07.9"],
    )
    types = sorted(e["type"] for e in categorize_errors(d))
    assert types == ["negation_error", "retrieval_failure", "wrong_specificity"]
    m = aggregate([d])
    assert m["precision"] == pytest.approx(1 / 3, abs=1e-3) and m["category_f1"] > m["f1"]


def test_retrieval_only_baseline_regression(kb, db):
    """Guards against regressions in extraction/retrieval/validation on the dev split."""
    report = evaluate(db, load_split(DATA_DIR / "dev.jsonl"), ["retrieval_only", "llm_only"])
    m = report["baselines"]["retrieval_only"]["metrics"]
    assert m["retrieval_recall"] >= 0.95
    assert m["f1"] >= 0.85, m
    assert m["unsupported_code_rate"] == 0.0
    assert report["baselines"]["llm_only"] == {"skipped": "requires LLM_PROVIDER=openrouter"}


def test_password_policy():
    with pytest.raises(ValueError):
        validate_password_strength("short")
    validate_password_strength("Long-enough-Pass1")


def test_jwt_expiry_and_tamper(monkeypatch):
    s = get_settings()
    token, _ = create_access_token("user-1", "coder")
    assert decode_access_token(token)["role"] == "coder"
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(token[:-2] + ("AA" if token[-2:] != "AA" else "BB"))
    monkeypatch.setattr(s, "access_token_minutes", -1)
    expired, _ = create_access_token("user-1", "coder")
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired)


def test_production_refuses_dev_secrets():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="Insecure production configuration"):
        Settings(environment="production")


def test_rate_limit(client):
    from app.api.routes import auth

    s = get_settings()
    old = s.login_rate_limit_per_minute
    s.login_rate_limit_per_minute = 2
    auth._attempts.clear()
    try:
        codes = [
            client.post(
                "/api/v1/auth/login", data={"username": "x@example.com", "password": "nope-nope-1A"}
            ).status_code
            for _ in range(3)
        ]
        assert codes == [401, 401, 429]
    finally:
        s.login_rate_limit_per_minute = old
        auth._attempts.clear()
        time.sleep(0)
