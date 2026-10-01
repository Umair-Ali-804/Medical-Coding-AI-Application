# n8n workflows

n8n is the **orchestrator only**. Every AI step (NLP, RAG, LLM, validation, confidence) is a single HTTP call to the FastAPI service. There are no Code nodes: with n8n 2.x task runners they can't read environment variables, and business logic belongs in the tested Python service.

| Workflow | Trigger | Steps |
|---|---|---|
| `01_document_intake.json` | `POST /webhook/clinical-document` (multipart field `file`; optional `encounter_type`, `external_id`) | upload → `/process` (extract, retrieve, code, validate, score) → IF mandatory review → alert (Slack/Teams webhook) or standard queue → respond `202` with `document_id`, `review_route`, `suggestions` |
| `02_platform_events.json` | `POST /webhook/medcoding-events` (the backend's `WEBHOOK_URL`) | HMAC signature check (Crypto node) → 401 or 200 → `document.finalized`: fetch export JSON → POST to `DOWNSTREAM_EXPORT_URL`; `document.failed` or mandatory route: alert |

## Setup

1. `docker compose up -d` imports both workflows (the `n8n-import` one-shot service).
2. Create an API key: `docker compose exec api python -m app.cli create-api-key n8n --role service`.
3. In n8n: **Credentials → New → Header Auth**, named `Medical Coding API key`, with header name `X-API-Key` and the key as the value.
4. Open each workflow, select that credential on every HTTP Request node that calls the API, save, and **publish/activate**.
5. Optional environment variables (set in `.env`): `REVIEW_ALERT_WEBHOOK_URL`, `DOWNSTREAM_EXPORT_URL`, `WEBHOOK_SECRET` (must match the backend).

Test:
```bash
curl -F "file=@data/raw/DOC015.txt" -F encounter_type=outpatient http://localhost:5678/webhook/clinical-document
```

Notes: alerts and exports carry identifiers and codes only, never clinical text. The HMAC is computed over `timestamp + "." + JSON.stringify(body)`, which matches the backend's compact JSON serialization.
