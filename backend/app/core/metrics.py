"""Prometheus metrics."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
PIPELINE_STAGE_LATENCY = Histogram(
    "pipeline_stage_duration_seconds",
    "Pipeline stage latency",
    ["stage"],
    buckets=(0.05, 0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)
PIPELINE_RUNS = Counter("pipeline_runs_total", "Pipeline runs", ["stage", "outcome"])
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens", ["model", "kind"])
LLM_COST = Counter("llm_cost_usd_total", "LLM cost in USD", ["model"])
LLM_ERRORS = Counter("llm_errors_total", "LLM call errors", ["model", "reason"])
SUGGESTIONS = Counter("coding_suggestions_total", "Suggestions generated", ["validation_status", "route"])
REVIEW_ACTIONS = Counter("review_actions_total", "Coder review actions", ["action"])
JOBS = Counter("jobs_total", "Background jobs", ["job_type", "outcome"])
