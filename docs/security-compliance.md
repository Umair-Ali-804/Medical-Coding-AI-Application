# Security & compliance

This document lists the controls the platform implements and the responsibilities that remain with the operator. It is not legal advice. Establish the requirements that apply in your market (for example HIPAA/HITECH in the US, GDPR in the EU, local health-data laws elsewhere) before processing real patient data.

## Implemented controls

### Identity and access
* JWT bearer tokens (HS256, `iss`, `exp`, `nbf`, `jti`), with a configurable lifetime.
* Roles: **admin** (everything, including reopening finalized documents), **coder** (review and finalize), **auditor** (read-only, including the audit trail), **service** (integrations: upload, process, read; can never approve).
* API keys are shown once and stored as SHA-256 hashes. They can be revoked, and last use is tracked.
* bcrypt (cost 12) password hashing, a password strength policy, per-IP login rate limiting, and constant-time behaviour for unknown users.
* The browser never sees the JWT: the Next.js BFF stores it in an `httpOnly`, `SameSite=Strict`, `Secure` cookie and attaches it server-side.

### Data protection
* **Encryption at rest:** all PHI-bearing columns (document text, entity text, evidence quotes, rationales, raw LLM responses) and stored original files are encrypted with Fernet (AES-128-CBC + HMAC-SHA256). `MultiFernet` supports rotation: prepend a new key to `ENCRYPTION_KEYS`, then run `python -m app.cli rotate-keys`. Also use encrypted volumes or disks at the infrastructure level.
* **In transit:** Caddy provides TLS in the production overlay, with HSTS on API responses in production.
* **Minimum necessary:** logs contain IDs, counts, codes and timings only; a formatter truncates long strings as a safety net. Webhooks and n8n alerts carry identifiers and codes, never clinical text. Clinical text is never searchable via SQL.
* **LLM provider:** requests set `provider.data_collection = "deny"` on OpenRouter so they only route to providers that don't retain prompts for training. **You still need a BAA / data processing agreement with OpenRouter and with every underlying provider before sending PHI**, or you should route to a self-hosted model (any OpenAI-compatible endpoint via `OPENROUTER_BASE_URL`). Embeddings run locally by default.
* `Cache-Control: no-store` on API responses. `X-Frame-Options`, `nosniff`, `Referrer-Policy` and CSP on the UI.

### Integrity and accountability
* **Audit trail:** every upload, view, extraction, coding run, review action, finalization, export, login (successful and failed), user/key change and job is recorded with actor, IP and request ID.
* **Tamper evidence:** each entry stores `sha256(prev_hash + canonical_entry)`. `GET /api/v1/audit/verify` (or `python -m app.cli verify-audit`) walks the chain. A Postgres trigger rejects `UPDATE` and `DELETE` on `audit_logs`.
* **Traceability:** model, prompt, retrieval, KB and pipeline versions are stored on every run; original vs. final code and error category are stored on every review.

### Application security
* Uploads: magic-byte type detection (not just the extension), size limits, rejection of binaries, PDF encryption handling.
* SSRF: per-request callback URLs must match `CALLBACK_ALLOWED_HOSTS`.
* The proxy route rejects path traversal. Strict Pydantic validation on all inputs.
* A production config guard: the API refuses to start with development secrets, a missing encryption key, a missing LLM key, or wildcard CORS.
* Containers run as non-root with `tini` as PID 1, and API docs are disabled in production.
* Outbound webhooks carry an HMAC-SHA256 signature (`X-MedCoding-Signature` over `timestamp.body`), which the n8n events workflow verifies.

## Operator responsibilities (checklist)

- [ ] Legal basis and agreements (BAA/DPA) with hosting, LLM and any other processors
- [ ] Risk assessment and a security review / penetration test before go-live
- [ ] SSO/OIDC and MFA for coders (put an identity-aware proxy in front, or extend `app/api/deps.py`)
- [ ] Network isolation: only the reverse proxy is public; restrict `/metrics`; firewall Qdrant and Postgres
- [ ] Encrypted disks and backups for Postgres, the Qdrant volume and the storage volume; tested restores
- [ ] Secrets in a secret manager (not a `.env` file on disk); key rotation schedule
- [ ] Retention policy: purge originals and clinical text after the retention period (the audit log is kept)
- [ ] Centralized log shipping with alerting on `auth.login_failed` spikes, audit chain failures, job failures
- [ ] Annual ICD-10-CM update (Oct 1) and quarterly HCPCS updates; re-run evaluation after every KB, prompt or model change
- [ ] Validate on de-identified data from your own specialties before trusting routing thresholds
- [ ] CPT: only load CPT content under a valid AMA license

## Licensing of coding content

| Code set | Source | Status in this repo |
|---|---|---|
| ICD-10-CM | CDC/NCHS tabular XML (US government work, public domain) | loaded automatically |
| HCPCS Level II | CMS quarterly files (public) | loader provided; file not bundled |
| CPT® | American Medical Association (copyrighted) | **not included**; loader requires `--i-have-a-cpt-license` |
| ICD-10-CM Official Guidelines | CMS/NCHS (public) | encoded as validation rules and prompt instructions |
