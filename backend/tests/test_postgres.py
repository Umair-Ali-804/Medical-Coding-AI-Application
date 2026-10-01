"""Postgres-only behaviour. Runs when TEST_POSTGRES_URL points at an empty, disposable database:

TEST_POSTGRES_URL=postgresql+psycopg://medcoding:medcoding@localhost:5432/medcoding_test pytest -m postgres
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

PG = os.environ.get("TEST_POSTGRES_URL")
pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not PG, reason="TEST_POSTGRES_URL not set")]
BACKEND = Path(__file__).resolve().parents[1]


def _alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": PG}
    subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, check=True, capture_output=True
    )


@pytest.fixture(scope="module")
def pg():
    _alembic("downgrade", "base")
    _alembic("upgrade", "head")
    eng = create_engine(PG)
    yield eng
    eng.dispose()


def test_migrations_roundtrip_and_no_drift(pg):
    env = {**os.environ, "DATABASE_URL": PG}
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "check"], cwd=BACKEND, env=env, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stdout + out.stderr


def test_audit_log_is_append_only(pg):
    with pg.begin() as c:
        c.execute(
            text(
                "INSERT INTO audit_logs (ts, actor_type, action, details, prev_hash, hash) "
                "VALUES (now(), 'system', 'test', '{}'::jsonb, :p, :h)"
            ),
            {"p": "0" * 64, "h": "a" * 64},
        )
    with pytest.raises(Exception, match="append-only"), pg.begin() as c:
        c.execute(text("UPDATE audit_logs SET action = 'tampered'"))
    with pytest.raises(Exception, match="append-only"), pg.begin() as c:
        c.execute(text("DELETE FROM audit_logs"))


def test_skip_locked_job_claiming(pg):
    """Two concurrent workers never claim the same job."""
    from sqlalchemy.orm import sessionmaker

    from app.models import Job
    from app.services import jobs

    S = sessionmaker(bind=pg)
    with S.begin() as s:
        for _ in range(2):
            s.add(Job(job_type="process_document", status="queued", payload={}))
    s1, s2 = S(), S()
    try:
        j1 = jobs.claim(s1, "w1")  # holds row lock until commit
        j2 = jobs.claim(s2, "w2")
        assert j1 and j2 and j1.id != j2.id
        s3 = S()
        assert jobs.claim(s3, "w3") is None  # both jobs are locked by other workers
        s3.rollback()
    finally:
        s1.rollback()
        s2.rollback()
