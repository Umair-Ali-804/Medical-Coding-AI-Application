"""Background worker: `python -m app.worker`.

Runs pipeline jobs from the Postgres queue. Scale horizontally by running more
replicas; SKIP LOCKED guarantees each job is processed by one worker at a time.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.core.metrics import JOBS
from app.db.session import session_scope
from app.models.enums import JobType
from app.services import jobs, pipeline
from app.services.principal import SYSTEM
from app.services.webhooks import send_event

log = logging.getLogger("worker")
_stop = threading.Event()


def _handle_signal(signum, _frame) -> None:  # noqa: ANN001
    log.info("worker_stopping", extra={"signal": signum})
    _stop.set()


def run_job(job_id: uuid.UUID, job_type: str, document_id: uuid.UUID | None) -> dict:
    with session_scope() as db:
        if job_type == JobType.EXTRACT:
            doc = pipeline.run_extraction(db, document_id, SYSTEM)
            return {"document_id": str(doc.id), "status": doc.status, "entities": len(doc.entities)}
        if job_type in (JobType.CODE, JobType.PROCESS_DOCUMENT):
            fn = pipeline.run_coding if job_type == JobType.CODE else pipeline.process_document
            summary = fn(db, document_id, SYSTEM)
            return {
                "document_id": str(summary.document_id),
                "suggestions": summary.suggestions,
                "review_route": summary.route,
                "by_route": summary.by_route,
            }
    raise ValueError(f"Unknown job type {job_type}")


def process_one(worker_id: str) -> bool:
    with session_scope() as db:
        job = jobs.claim(db, worker_id)
        if job is None:
            return False
        job_id, job_type, doc_id, callback = job.id, job.job_type, job.document_id, job.callback_url
    log.info("job_started", extra={"job_id": str(job_id), "job_type": job_type})
    try:
        result = run_job(job_id, job_type, doc_id)
    except Exception as exc:  # noqa: BLE001
        retryable = not isinstance(exc, AppError) or exc.status_code >= 500
        log.warning(
            "job_failed",
            extra={"job_id": str(job_id), "error_type": type(exc).__name__, "retryable": retryable},
        )
        with session_scope() as db:
            job = jobs.get(db, job_id)
            jobs.fail(db, job, f"{type(exc).__name__}: {exc}", retryable=retryable)
            final = job.status == "failed"
        if final and doc_id:
            pipeline.mark_failed(doc_id, SYSTEM, job_type, exc)
            send_event(
                "document.failed",
                {"document_id": str(doc_id), "job_id": str(job_id), "error": type(exc).__name__},
                url=callback,
            )
        JOBS.labels(job_type, "failed" if final else "retry").inc()
        return True
    with session_scope() as db:
        jobs.succeed(db, jobs.get(db, job_id), result)
    JOBS.labels(job_type, "succeeded").inc()
    log.info("job_succeeded", extra={"job_id": str(job_id), "job_type": job_type})
    send_event(
        "document.coded" if job_type != JobType.EXTRACT else "document.extracted",
        {"job_id": str(job_id), **result},
        url=callback,
    )
    return True


def _loop(worker_id: str) -> None:
    s = get_settings()
    while not _stop.is_set():
        try:
            busy = process_one(worker_id)
        except Exception:  # noqa: BLE001 - keep the worker alive (e.g. DB restart)
            log.exception("worker_loop_error")
            busy = False
        if not busy:
            _stop.wait(s.worker_poll_interval_s)


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    base = f"{socket.gethostname()}:{os.getpid()}"
    threads = [
        threading.Thread(target=_loop, args=(f"{base}:{i}",), daemon=True)
        for i in range(s.worker_concurrency)
    ]
    log.info("worker_started", extra={"concurrency": s.worker_concurrency, "llm_provider": s.llm_provider})
    for t in threads:
        t.start()
    while not _stop.is_set():
        time.sleep(0.5)
    for t in threads:
        t.join(timeout=60)


if __name__ == "__main__":
    main()
