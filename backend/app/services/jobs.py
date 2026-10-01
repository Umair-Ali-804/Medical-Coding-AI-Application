"""Durable Postgres-backed job queue.

Claiming uses `SELECT ... FOR UPDATE SKIP LOCKED`, so any number of worker
replicas can run safely. Failed jobs retry with exponential backoff; jobs whose
worker died are reclaimed after JOB_LOCK_TIMEOUT_S.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFound
from app.db.base import utcnow
from app.models import Job
from app.models.enums import JobStatus, JobType

log = logging.getLogger(__name__)


def enqueue(
    db: Session,
    job_type: JobType,
    document_id: uuid.UUID | None,
    *,
    created_by: str | None,
    payload: dict | None = None,
    callback_url: str | None = None,
) -> Job:
    job = Job(
        job_type=job_type,
        document_id=document_id,
        payload=payload or {},
        created_by=created_by,
        max_attempts=get_settings().job_max_attempts,
        callback_url=callback_url,
        status=JobStatus.QUEUED,
    )
    db.add(job)
    db.flush()
    return job


def get(db: Session, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFound("Job not found")
    return job


def claim(db: Session, worker_id: str) -> Job | None:
    s = get_settings()
    now = utcnow()
    stale = now - timedelta(seconds=s.job_lock_timeout_s)
    stmt = (
        select(Job)
        .where(
            or_(
                (Job.status == JobStatus.QUEUED) & (Job.run_after <= now),
                (Job.status == JobStatus.RUNNING) & (Job.locked_at < stale),
            )
        )
        .order_by(Job.run_after)
        .limit(1)
    )
    if db.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    job = db.execute(stmt).scalar_one_or_none()
    if job is None:
        return None
    job.status, job.locked_by, job.locked_at = JobStatus.RUNNING, worker_id, now
    job.started_at = job.started_at or now
    job.attempts += 1
    db.flush()
    return job


def succeed(db: Session, job: Job, result: dict) -> None:
    job.status, job.result, job.error = JobStatus.SUCCEEDED, result, None
    job.finished_at, job.locked_by, job.locked_at = utcnow(), None, None


def fail(db: Session, job: Job, error: str, *, retryable: bool = True) -> None:
    job.error = error[:4000]
    job.locked_by, job.locked_at = None, None
    if retryable and job.attempts < job.max_attempts:
        job.status = JobStatus.QUEUED
        job.run_after = utcnow() + timedelta(seconds=min(600, 15 * 2 ** (job.attempts - 1)))
    else:
        job.status, job.finished_at = JobStatus.FAILED, utcnow()
