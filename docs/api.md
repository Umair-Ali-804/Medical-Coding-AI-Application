# API reference

Interactive docs: `/docs` (non-production). Machine-readable spec: [`openapi.json`](openapi.json).

Authentication: `Authorization: Bearer <JWT>` from `POST /api/v1/auth/login` (form `username`, `password`), or `X-API-Key: mcai_...` for integrations (service role).

Errors: `{"error": {"code", "message", "details", "request_id"}}` with conventional HTTP status codes (401, 403, 404, 409 state conflict, 413, 415, 422, 429, 502 upstream LLM).

| Method | Path | Summary | Tag |
|---|---|---|---|
| POST | `/api/v1/auth/login` | Login | auth |
| GET | `/api/v1/auth/me` | Me | auth |
| GET | `/api/v1/users` | List Users | admin |
| POST | `/api/v1/users` | Create User | admin |
| PATCH | `/api/v1/users/{user_id}` | Update User | admin |
| GET | `/api/v1/api-keys` | List Keys | admin |
| POST | `/api/v1/api-keys` | Create Key | admin |
| DELETE | `/api/v1/api-keys/{key_id}` | Revoke Key | admin |
| POST | `/api/v1/documents` | Upload a clinical document (PDF, DOCX, TXT) | documents |
| GET | `/api/v1/documents` | List Documents | documents |
| POST | `/api/v1/documents/text` | Submit clinical text directly (JSON) | documents |
| GET | `/api/v1/documents/{doc_id}` | Get Document | documents |
| POST | `/api/v1/documents/{doc_id}/extract` | Clinical information extraction | documents |
| POST | `/api/v1/documents/{doc_id}/code` | RAG + LLM coding + validation + confidence | documents |
| POST | `/api/v1/documents/{doc_id}/process` | Extraction + coding in one call | documents |
| GET | `/api/v1/documents/{doc_id}/suggestions` | Suggestions | documents |
| POST | `/api/v1/documents/{doc_id}/suggestions` | Coder adds a code the AI missed | documents |
| POST | `/api/v1/documents/{doc_id}/finalize` | Finalize | documents |
| POST | `/api/v1/documents/{doc_id}/reopen` | Reopen | documents |
| GET | `/api/v1/documents/{doc_id}/audit` | Document Audit | documents |
| GET | `/api/v1/documents/{doc_id}/reviews` | Document Reviews | documents |
| GET | `/api/v1/documents/{doc_id}/runs` | Document Runs | documents |
| GET | `/api/v1/documents/{doc_id}/export` | Export final codes (JSON or CSV) | documents |
| GET | `/api/v1/suggestions/{sid}` | Get Suggestion | review |
| POST | `/api/v1/suggestions/{sid}/approve` | Approve | review |
| POST | `/api/v1/suggestions/{sid}/reject` | Reject | review |
| POST | `/api/v1/suggestions/{sid}/edit` | Edit | review |
| GET | `/api/v1/knowledge/versions` | Kb Versions | knowledge |
| GET | `/api/v1/knowledge/search` | Kb Search | knowledge |
| GET | `/api/v1/knowledge/codes/{system}/{code}` | Kb Code | knowledge |
| GET | `/api/v1/jobs/{job_id}` | Get Job | jobs |
| GET | `/api/v1/stats` | Stats | stats |
| GET | `/api/v1/audit` | Audit Log | audit |
| GET | `/api/v1/audit/verify` | Audit Verify | audit |
| GET | `/api/v1/health/live` | Live | health |
| GET | `/api/v1/health/ready` | Ready | health |

## Typical flows

**Synchronous (UI, small volumes)**
```
POST /api/v1/documents            (multipart file)   -> {document_id}
POST /api/v1/documents/{id}/process                 -> {suggestions, review_route, by_route}
GET  /api/v1/documents/{id}/suggestions
POST /api/v1/suggestions/{sid}/approve | /edit | /reject
POST /api/v1/documents/{id}/finalize                -> final codes
GET  /api/v1/documents/{id}/export?format=csv|json
```

**Asynchronous (integrations, batch)**
```
POST /api/v1/documents/text  {text, process: true, callback_url?}  -> {document_id, job_id}
GET  /api/v1/jobs/{job_id}                                     (or wait for the signed webhook)
```

**Webhook events** (to `WEBHOOK_URL` or the per-request `callback_url`): `document.coded`, `document.extracted`, `document.failed`, `document.finalized`. Headers: `X-MedCoding-Event`, `X-MedCoding-Timestamp`, `X-MedCoding-Signature = hex(HMAC_SHA256(WEBHOOK_SECRET, timestamp + '.' + body))`. Payloads carry identifiers and codes only.
