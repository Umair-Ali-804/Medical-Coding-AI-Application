# Operations runbook

## Deploy

```bash
cp .env.example .env && python3 scripts/gen_secrets.py   # fill .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose logs -f migrate      # first boot: migrations + ICD-10-CM load + vector index
```

`migrate` is a one-shot service. `api` and `worker` start only after it succeeds. Re-running it is safe: migrations are idempotent, and the KB bootstrap loads or indexes only what is missing, checking the real vector count in Qdrant.

## Health and monitoring

| Endpoint | Use |
|---|---|
| `GET /health/live` | liveness (process up) |
| `GET /health/ready` | readiness: database, active KB, vector store. Returns 503 when degraded |
| `GET /metrics` | Prometheus (keep internal) |

Key metrics: `http_request_duration_seconds`, `pipeline_stage_duration_seconds{stage}`, `pipeline_runs_total{stage,outcome}`, `llm_tokens_total`, `llm_cost_usd_total`, `llm_errors_total{reason}`, `coding_suggestions_total{validation_status,route}`, `review_actions_total{action}`, `jobs_total{job_type,outcome}`.

Suggested alerts: readiness failing for more than 2 minutes; `llm_errors_total` rate above 5%; `jobs_total{outcome="failed"}` increasing; queued jobs older than 15 minutes (`SELECT count(*) FROM jobs WHERE status='queued' AND run_after < now() - interval '15 min'`); audit chain verification failing.

## Routine tasks

**Users and keys**
```bash
docker compose exec api python -m app.cli create-user jane@org --role coder
docker compose exec api python -m app.cli create-api-key billing-bridge --role service
```
Or use the admin API: `/api/v1/users`, `/api/v1/api-keys`.

**Annual ICD-10-CM update (effective October 1)**
1. Download `icd10cm-tabular-<YEAR>.xml` from CDC (or upgrade `simple-icd-10-cm`).
2. Load and index the new version (loading activates it; the previous version stays in the database for lookups):
   `python -m app.cli kb load-icd10cm --xml /path/icd10cm-tabular-2027.xml`, then `python -m app.cli kb index`. Do this in a maintenance window, or ahead of time on a staging copy.
3. Restart `api` and `worker` so they pick up the new snapshot.
4. Re-run the evaluation on validation and test and compare against the previous report.
5. Codes that were valid in an earlier version are reported by validation as "not valid in 2027 (exists in: 2026)".

**HCPCS / CPT**
```bash
python -m app.cli kb load-hcpcs HCPC2026_JAN_ANWEB.xlsx --version 2026Q1
python -m app.cli kb load-cpt cpt_licensed.csv --version 2026 --i-have-a-cpt-license
# then add to ENABLED_CODE_SYSTEMS, e.g. ICD-10-CM,HCPCS
```

**Encryption key rotation**
1. Generate a new key and prepend it: `ENCRYPTION_KEYS=<new>,<old>`.
2. Restart services, then run `docker compose exec api python -m app.cli rotate-keys` (re-encrypts every PHI column and every stored original with the new key).
3. Remove the old key once backups encrypted with it have expired.

**Audit verification**: `docker compose exec api python -m app.cli verify-audit` (exits non-zero if the chain is broken).

**Recalibration** (after collecting more reviewed data, or after changing model or prompt):
```bash
python -m app.evaluation.runner --split validation --calibrate /config/calibration.json
# set CALIBRATION_FILE=/config/calibration.json and restart
```

## Backups

* Postgres: nightly `pg_dump -Fc` plus WAL archiving for point-in-time recovery. This holds documents (encrypted), suggestions, reviews, audit and KB.
* Storage volume: original files (encrypted). Back up together with the database.
* Qdrant: rebuildable from the KB (`kb bootstrap`). Snapshots are optional.
* **Keys:** back up `ENCRYPTION_KEYS` separately and securely. Without them the data cannot be recovered.

## Troubleshooting

| Symptom | Check |
|---|---|
| Documents stuck in `processing` | `worker` logs; `SELECT status, attempts, error FROM jobs ORDER BY created_at DESC LIMIT 20` |
| `failed` status with `UpstreamError` | OpenRouter key, credit or model name; `llm_errors_total{reason}` |
| Readiness shows `knowledge_base: missing` | run `python -m app.cli kb bootstrap` |
| Retrieval version ends with `:lexical-only` | Qdrant unreachable or the collection is empty; run `kb bootstrap` |
| 422 "no usable text layer" | scanned PDF; configure an OCR provider |
| Login 429 | per-IP rate limit (`LOGIN_RATE_LIMIT_PER_MINUTE`) |
| n8n signature check fails | `WEBHOOK_SECRET` must match `MEDCODING_WEBHOOK_SECRET` in n8n |
