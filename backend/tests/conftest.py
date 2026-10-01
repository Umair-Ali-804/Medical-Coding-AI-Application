"""Test fixtures.

Unit/integration tests run on SQLite with the REAL ICD-10-CM FY2026 tabular (loaded once per
session), a deterministic hash embedder, an in-memory vector store and the heuristic coder, so
the suite is hermetic and needs no network. Postgres-specific behaviour (migrations, SKIP
LOCKED, audit trigger) is covered by tests marked `postgres` when TEST_POSTGRES_URL is set.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="medcoding-test-"))
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite:///{_TMP / 'test.db'}",
        "EMBEDDING_PROVIDER": "hash",
        "VECTOR_STORE": "memory",
        "LLM_PROVIDER": "heuristic",
        "ENABLED_CODE_SYSTEMS": "ICD-10-CM",
        "STORAGE_DIR": str(_TMP / "storage"),
        "LOG_JSON": "false",
        "LOG_LEVEL": "WARNING",
        "LOGIN_RATE_LIMIT_PER_MINUTE": "1000",
        "BOOTSTRAP_ADMIN_EMAIL": "",
        "WEBHOOK_URL": "",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import get_engine, session_scope  # noqa: E402
from app.models import Base, User  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "evaluation"
PASSWORD = "Sup3r-Secret-Pass!"


@pytest.fixture(scope="session", autouse=True)
def database():
    get_settings.cache_clear()
    engine = get_engine()
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def kb(database):
    """Load the official ICD-10-CM tabular into the test DB once."""
    from app.knowledge import repository
    from app.knowledge.icd10cm import default_tabular_path, parse_tabular

    path = default_tabular_path()
    if path is None:
        pytest.skip("simple-icd-10-cm (ICD-10-CM tabular XML) not installed")
    version, records = parse_tabular(path)
    with session_scope() as db:
        repository.load_records(db, "ICD-10-CM", version, records, source="test", checksum="test")
    with session_scope() as db:
        snap = repository.get_snapshot(db, "ICD-10-CM")
    return snap


@pytest.fixture()
def db(database):
    from app.db.session import get_sessionmaker

    s = get_sessionmaker()()
    yield s
    s.rollback()
    s.close()


def _user(email: str, role: str) -> None:
    with session_scope() as db:
        if not db.query(User).filter_by(email=email).one_or_none():
            db.add(
                User(email=email, full_name=role.title(), role=role, hashed_password=hash_password(PASSWORD))
            )


@pytest.fixture(scope="session")
def users(database):
    for email, role in (
        ("admin@example.com", "admin"),
        ("coder@example.com", "coder"),
        ("auditor@example.com", "auditor"),
    ):
        _user(email, role)
    return {"admin": "admin@example.com", "coder": "coder@example.com", "auditor": "auditor@example.com"}


@pytest.fixture(scope="session")
def client(kb, users):
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def login(client: TestClient, email: str) -> dict:
    r = client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def coder_headers(client, users):
    return login(client, users["coder"])


@pytest.fixture(scope="session")
def admin_headers(client, users):
    return login(client, users["admin"])


@pytest.fixture(scope="session")
def auditor_headers(client, users):
    return login(client, users["auditor"])
