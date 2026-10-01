# Medical Coding AI: pre-bill coding and claim-integrity assistant

**Clinical note → evidence-backed ICD-10-CM diagnoses + CPT/HCPCS/ICD-10-PCS procedures → official-guideline citations → deterministic validation → calibrated confidence → coder approval → draft claim → pre-bill denial check (MUE, NCCI, modifiers, validity on the date of service...) → audited export.**

Real-world goal: fewer denials, less coder time per chart, fewer missed charges, and every code traceable to documentation and guidelines. See **[docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md)** (problem, solution, results, KPIs, roadmap) and **[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)** (data you have, data missing, where to get it).

**Deploy it (free pilot server or production): [DEPLOY.md](DEPLOY.md).**

**Run it on Google Colab:** [colab/COLAB_COMMANDS.md](colab/COLAB_COMMANDS.md) / `colab/MedicalCodingAI_Colab.ipynb`.

The LLM never has the last word. Every suggestion is grounded in retrieved official reference text, checked by a rule engine that cannot hallucinate, scored by the platform (not by the model's self-reported confidence), and approved, edited or rejected by a human coder. Every step is versioned and written to a tamper-evident audit trail.

```mermaid
flowchart LR
    UI[Next.js review UI] -->|BFF proxy, httpOnly session| API
    N8N[n8n orchestrator] -->|X-API-Key, service role| API
    subgraph API[FastAPI service]
      ING[Ingestion<br/>PDF/DOCX/TXT, cleaning, sections] --> NLP[Clinical NLP<br/>lexicon + problem list + LLM<br/>ConText negation/uncertainty/family]
      NLP --> RAG[Hybrid RAG<br/>Qdrant dense + BM25, RRF]
      RAG --> LLM[OpenRouter LLM<br/>strict JSON schema]
      LLM --> VAL[Validation engine<br/>exists, billable, evidence, negation,<br/>Excludes1, laterality, sex/age...]
      VAL --> CONF[Confidence + routing]
    end
    API --> PG[(PostgreSQL<br/>encrypted PHI, audit chain, job queue)]
    RAG --> QD[(Qdrant)]
    WORKER[Worker replicas] --> PG
    API -. signed webhooks .-> N8N
```

![Review workspace: highlighted clinical text with negated findings struck through, evidence-linked ICD-10-CM suggestions with validation status and platform confidence](docs/screenshots/review-workspace.png)

## What's in the box

| Area | Implementation |
|---|---|
| Ingestion | PDF (text layer), DOCX, TXT, magic-byte type detection, size limits, scanned-PDF detection with an OCR provider interface, text normalization, clinical section detection (SOAP, H&P, discharge summary, op note) |
| Clinical NLP | Curated lexicon (~390 terms, abbreviation-safe), problem-list parser with combination-diagnosis handling, **ConText/NegEx** assertion engine (negated, uncertain, historical, family, hypothetical), laterality/severity/temporal modifiers, optional LLM extraction reconciled against ConText (disagreements are flagged, never silently resolved) |
| Knowledge base | **Official CDC ICD-10-CM FY2026 tabular** (98,262 codes, 74,795 billable) with 7th-character expansion and inherited instructional notes (Excludes1/2, code first, use additional code). HCPCS Level II loader (CMS file). CPT loader gated behind an explicit AMA-license acknowledgement; no CPT content is shipped |
| RAG | Qdrant dense retrieval (local ONNX `bge-small-en-v1.5`, baked into the image) + in-process BM25, reciprocal rank fusion, coding-convention priors (NOS over NEC, chapter/episode guards) |
| LLM | OpenRouter, strict `json_schema` output, Pydantic validation with one repair round, retries with backoff, cost/token accounting, `data_collection: deny` provider routing, versioned prompts, optional second model for agreement scoring |
| Validation | Code exists (and in which version), billable, verbatim evidence (anti-hallucination), negation/uncertainty/family contradictions, NLP-vs-LLM assertion conflicts, Excludes1 conflicts, unspecified-with-specific, laterality mismatch, sex/age edits, manifestation sequencing, duplicates, unlicensed code systems, use-additional-code hints |
| Confidence | Platform score from evidence, retrieval, validation, entity match, model agreement and (low-weight) LLM self-report; weights and review threshold **fitted on a validation split**, not guessed |
| Review | Approve / edit (with KB search) / reject with reason + error category / add missed code / finalize / admin reopen. Only human coders can approve; integrations cannot |
| Traceability | `model_runs` record provider, model, prompt version, retrieval version, KB version, tokens, cost, latency. `reviews` record original vs. final code and error category |
| Security | JWT + RBAC (admin, coder, auditor, service), API keys (hashed), bcrypt, login rate limiting, Fernet encryption of all PHI columns and stored files with key rotation, hash-chained audit log with a DB trigger that forbids UPDATE/DELETE, PHI-free logs and webhooks, SSRF-safe callbacks, security headers, production config guard |
| Operations | Postgres job queue (`SKIP LOCKED`, retries, stale-lock recovery), horizontally scalable workers, Prometheus metrics, JSON logs with request IDs, health/readiness probes, Alembic migrations |
| Reference data | `kb load-all` loads the official CMS/CDC files as published: ICD-10-CM order/codes files (validity authority), ICD-10-PCS, HCPCS ANWEB fixed-width (+ modifiers, coverage codes, termination dates, processing notes), NCCI MUE, NCCI PTP, CPT CSV (licensed), fee schedule, LCD/NCD coverage crosswalk, Official Guidelines PDFs. Every set has a validity window; `kb freshness` warns before it expires |
| Procedures | CPT/HCPCS (outpatient) and ICD-10-PCS (inpatient) suggestions for procedures documented as performed; drug units inferred from documented dose |
| Guidelines RAG | ICD-10-CM and ICD-10-PCS Official Guidelines split into citable passages (`I.C.4.a`, `B3.1b`...), attached to each suggestion and given to the LLM |
| Claim scrubber | 30+ deterministic pre-bill rules with typical CARC/RARC, fix and source; risk score and dollars at risk (`POST /api/v1/claims/scrub`, `GET /api/v1/documents/{id}/claim-check`, `python -m app.cli claims scrub`) |
| Workbench | AAXIS AI-branded web UI (custom HTML/CSS on Gradio, dark graphite + silver theme, mobile friendly) for Colab/pilots: code a note, scrub a claim, code lookup (with MUE/notes/Excludes), guideline search, reference-data status. **Download branded PDF reports** of coded notes, claim checks and code sheets |
| Evaluation | 4 baselines from the plan (LLM only → +RAG → +validation → full) plus a no-LLM retrieval baseline, precision/recall/F1, category F1, exact match, unsupported-code rate, retrieval recall, auto-accept precision, latency, cost, error taxonomy, threshold calibration |
| Automation | n8n workflows: document intake (webhook → upload → process → route → alert → respond) and platform events (HMAC-verified → finalized-code export / failure alerts) |

## Quick start (Docker)

```bash
python scripts/setup_env.py           # creates .env with generated secrets; asks for your OpenRouter key
docker compose up -d --build
docker compose logs -f migrate         # wait for "knowledge base ready" (first run: 5-10 min)
```

First boot runs migrations, loads ICD-10-CM and builds the vector index (a few minutes). Then:

- Review UI: http://localhost:3000 (sign in with the admin email and password printed by `setup_env.py`)
- API docs (non-production): http://localhost:8000/docs
- n8n: http://localhost:5678

Create a coder and an integration key:

```bash
docker compose exec api python -m app.cli create-user coder@example.org --role coder
docker compose exec api python -m app.cli create-api-key n8n --role service
```

In n8n, create a **Header Auth** credential named `Medical Coding API key` (header `X-API-Key`, value = the key above), open the two imported workflows, select that credential on the HTTP nodes, and activate them. Documents can then be sent to `POST http://localhost:5678/webhook/clinical-document` (multipart field `file`).

Production: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` adds Caddy TLS termination and removes directly published ports. See [docs/operations.md](docs/operations.md).

## Local development

```bash
cd backend
pip install -e ".[dev,kb,embeddings]"
export DATABASE_URL=postgresql+psycopg://medcoding:medcoding@localhost:5432/medcoding
alembic upgrade head
python -m app.cli kb bootstrap            # load ICD-10-CM + build vector index
python -m app.cli create-user you@example.org --role admin
uvicorn app.main:app --reload             # API on :8000
python -m app.worker                      # background jobs

cd ../frontend && npm ci && BACKEND_URL=http://localhost:8000 npm run dev   # UI on :3000
```

Without an OpenRouter key, `LLM_PROVIDER=heuristic` runs the full pipeline with the deterministic retrieval-only coder, which is useful for development and as a baseline.

Load your reference files (see `data_sources/README.md`):

```bash
python -m app.cli kb load-all --data-dir ../data_sources [--i-have-a-cpt-license]
python -m app.cli kb index --system all
python -m app.cli kb freshness --dos 2026-10-01
python -m app.cli code-note ../data/sample_notes/01_office_visit_knee_injection.txt --sex M --age 67
python -m app.demo.gradio_app            # workbench UI on :7860
```

Tests: `cd backend && pytest` (127 tests; set `TEST_POSTGRES_URL` to also run the Postgres-specific tests).

## Evaluation

```bash
cd backend
python -m app.evaluation.runner --split dev --baselines all
python -m app.evaluation.runner --split validation --calibrate calibration.json   # fit weights + threshold
CALIBRATION_FILE=calibration.json python -m app.evaluation.runner --split test --out test-report.json
```

Claim scrubber: `python -m app.evaluation.claims_eval` (26 labeled claims, `data/evaluation/claims_scrub_cases.jsonl`).

### Results (pipeline 1.1, no-LLM retrieval-only mode, FY2026 ICD-10-CM)

Five labeled sets of synthetic notes, every gold code checked against the CMS FY2026 order file and assigned by
the Official Guidelines. `optional_codes` (e.g. external-cause codes) are neither required nor penalized.

| Set | Notes | Role | Original 1.0 | **1.1 F1** | Precision | Recall |
|---|---|---|---|---|---|---|
| dev | 12 | tuning (original) | 1.00 | **1.00** | 1.00 | 1.00 |
| validation | 12 | calibration (original) | 0.88 | **0.96** | 0.96 | 0.96 |
| test | 12 | held out (original) | 0.94 | **1.00** | 1.00 | 1.00 |
| realworld | 31 | guideline-focused; used to build the rules | 0.61 | 0.99 | 0.98 | 1.00 |
| blind | 24 | written before the rules; **0.84 on its single blind run**, later used for fixes | 0.52 | 0.98 | 0.98 | 0.98 |
| **blind2** | 18 | **final blind check**, written after all rules, mostly conditions the rules do not target | 0.60 | **0.73** | 0.81 | 0.67 |

How to read this: `blind2` is the honest out-of-sample estimate for the **offline** coder. The guideline rules fix
guideline-driven errors (combination codes, history vs acute, 7th characters, Z79 drug therapy, documentation support),
but general specificity in offline mode still depends on top-1 retrieval. **Production accuracy must come from the
LLM mode + human coder review**, measured on your own de-identified charts:
`python -m app.evaluation.runner --split blind2 --baselines full` (needs `LLM_PROVIDER=openrouter`).
LLM baselines could not be run in the build environment (no network access to the model provider).

What changed in 1.1 (all covered by tests):
* `app/coding/guideline_rules.py`: deterministic coder rules applied after any model, each change cited to the
  Official Guidelines / Alphabetic Index, with verbatim evidence, recorded in `model_runs.config.guideline_rule_changes`.
  Hypertension with heart failure/CKD (I11/I12/I13 + I50/N18/Z99.2), diabetes with CKD/foot ulcer/retinopathy/
  hypo-/hyperglycemia, Z79.4/Z79.84/Z79.85/Z79.01/Z79.81-, history of MI (I25.2) and stroke (Z86.73/I69), tobacco
  (F17.210 vs Z87.891), normal pregnancy (Z34 + Z3A), Alzheimer's dementia (G30 + F02), identified influenza (J10),
  asthma/COPD exacerbation, OA/sciatica laterality, injury 7th character, sepsis organism, integral symptoms,
  screening/annual-exam encounters, unspecified-with-specific cleanup.
* **Documentation support** (anti-hallucination and anti-upcoding): a code is removed when its title requires something
  the note does not document: organism, anatomic site, complication, diabetes type, malignancy, "with coma/
  perforation/gangrene...", and high-risk CC/MCC conditions (respiratory failure, sepsis, AKI, encephalopathy,
  malnutrition, shock).
* Injury phrases are extracted whole ("Closed displaced Colles fracture, right radius, initial encounter").
* ConText sentences survive hard-wrapped lines, so negations reach the next line.
* Inpatient uncertain diagnoses at discharge are coded (Guideline II.H); outpatient ones are not.
* Charts are routed to **mandatory review** whenever an active code set has expired for today's date;
  `/health/ready` reports `reference_data`.

## Project layout

```
backend/            FastAPI service, worker, CLI, Alembic migrations, tests
  app/ingestion     extraction, cleaning, sections, encrypted storage
  app/extraction    lexicon, problem-list parser, ConText, LLM reconciliation
  app/knowledge     ICD-10-CM tabular parser, HCPCS/CPT loaders, versioned KB
  app/rag           embeddings, Qdrant/memory stores, BM25, hybrid retriever
  app/llm           OpenRouter client, schemas, versioned prompts
  app/coding        concept grouping, derived codes, engine, coding models
  app/validation    deterministic rule engine, evidence matching
  app/confidence    scorer, calibration
  app/evaluation    baselines, metrics, error analysis
  app/services      pipeline, review, audit, jobs, webhooks
frontend/           Next.js 16 review workspace (BFF proxy, httpOnly session)
n8n/workflows/      importable n8n workflows
data/evaluation/    synthetic dataset splits (dev / validation / test) + labeled claims for the scrubber
data/sample_notes/  synthetic notes with procedures (office injection, inpatient lap chole, laceration)
data_sources/       your CMS/CDC reference files (FY2026 / Q3 2026)
colab/              Colab notebook + command list
  (backend) app/claims       pre-bill claim scrubber
  (backend) app/knowledge    + cms_loaders, edits (MUE/PTP/notes), guidelines, loader_service
  (backend) app/coding       + procedures (CPT/HCPCS/PCS)
  (backend) app/demo         code_note service + Gradio workbench
scripts/            dataset builder
docs/               architecture, security & compliance, operations, evaluation
```

## Important

This software is decision support for certified coders, not an autonomous coding system. Before processing real patient data, establish the applicable legal and compliance requirements for your market (for example HIPAA and a BAA with every processor, including your LLM provider), run a security review, and validate accuracy on your own de-identified data and specialties. CPT® is a registered trademark of the American Medical Association; use CPT content only under a valid license.
