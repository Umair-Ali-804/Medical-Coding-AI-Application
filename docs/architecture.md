# Architecture

## Principles

1. **n8n orchestrates, Python decides.** n8n handles triggers, routing and notifications. All NLP, retrieval, LLM calls, validation and scoring live in the FastAPI service, where they are versioned, tested and evaluated.
2. **Retrieve, then reason.** The LLM chooses among retrieved official references and must quote verbatim evidence. It is never asked "what is the code?" from memory alone.
3. **Never trust the model.** A deterministic validation engine checks every proposal, and the platform computes its own confidence from independent signals.
4. **The coder decides.** Integrations cannot approve codes. Finalization requires every suggestion to be reviewed.
5. **Everything is attributable.** Each output is tied to model, prompt, retrieval and KB versions, and every action is in a hash-chained audit log.

## Pipeline

```
upload ─► detect type (magic bytes) ─► extract text (PDF text layer | DOCX | TXT | OCR provider)
       ─► clean (unicode, hyphenation, page footers) ─► sections (SOAP / H&P / discharge / op note)
       ─► entities: lexicon + problem-list parser (+ LLM extraction)  ─► ConText assertions
       ─► concept groups (merge mentions, fold vague mentions into specific diagnoses,
          derive Z-codes: long-term drug therapy, BMI with weight diagnosis, family history)
       ─► hybrid retrieval per concept (Qdrant dense + BM25, RRF, coding-convention priors)
       ─► coding model (OpenRouter LLM with strict JSON schema | heuristic baseline)
       ─► validation engine ─► confidence + routing ─► suggestions (pending review)
       ─► coder: approve / edit / reject / add ─► finalize ─► final_codes ─► export / webhook
```

### Stage responsibilities

| Stage | Module | Output | Failure behaviour |
|---|---|---|---|
| Ingestion | `app/ingestion` | cleaned text, sections, encrypted original | 415/413/422 at upload; scanned PDFs require an OCR provider |
| Extraction | `app/extraction` | `clinical_entities` with assertions and modifiers | LLM extraction failure degrades to rules-only (recorded as a warning on the model run) |
| Retrieval | `app/rag` | top-k candidates with scores and sources | vector store down → lexical-only (retrieval version records `:lexical-only`) |
| Coding | `app/coding`, `app/llm` | proposals with evidence and rationale | retries with backoff; invalid JSON gets one repair round; then the document is marked failed |
| Validation | `app/validation` | status (`passed`/`flagged`/`rejected`) + issues | deterministic, no external calls |
| Confidence | `app/confidence` | score, signal breakdown, route | calibration file optional; priors otherwise |
| Review | `app/services/review.py` | reviews, final codes | role checks, state checks (finalized documents are locked) |

### Why ConText and the LLM both assess negation

LLMs occasionally miss negation ("denies chest pain" → chest pain). ConText is deterministic and well studied, but it misses phrasing outside its trigger lists. The extractor runs both on the same span. If they disagree, the entity is marked `assertion_conflict`, the conservative value wins (negated/uncertain), and the resulting suggestion is forced into mandatory review. `tests/test_pipeline_llm.py` shows this catching an LLM negation error end to end.

### Retrieval details

* **Documents:** every billable code of the active KB version. The text is the description plus inclusion terms.
* **Dense:** `BAAI/bge-small-en-v1.5` via fastembed (ONNX, CPU), cosine similarity in Qdrant. The model is baked into the image, so no query or PHI leaves the host.
* **Lexical:** BM25 over the same text with clinical synonym expansion (HTN, CKD, COPD...).
* **Fusion:** reciprocal rank fusion, then a blended score of fused rank, query coverage and description precision, plus priors that encode coding conventions: unspecified/NOS beats other/NEC when no subtype is documented; the pregnancy (O) and perinatal (P) chapters need pregnancy/newborn context; the sequela/subsequent-encounter 7th characters need explicit documentation; the main term leads the title.
* **Combination phrases** ("sepsis due to UTI") also retrieve for their head concept, so a separately coded condition isn't lost.

### Confidence

```
score = sigmoid(b + w_e·evidence + w_r·retrieval + w_v·validation + w_n·entity + w_a·agreement + w_l·llm)
```

| Signal | Meaning |
|---|---|
| evidence | 1.0 when the quote is verbatim in the note; the fuzzy ratio otherwise |
| retrieval | retrieval score × rank factor; 0 if RAG never surfaced the code |
| validation | 1.0 passed, 0.1 to 0.4 flagged (by number of flags), 0 rejected |
| entity | similarity between the affirmed linked entity and the code description |
| agreement | optional second model: 1.0 same code, 0.5 same category, 0 none |
| llm | the model's self-reported probability (low weight) |

`python -m app.evaluation.runner --split validation --calibrate cal.json` fits the weights (L2-regularized logistic regression, shrunk toward the priors) once there is enough data, and picks the **lowest threshold that reaches the target precision (default 95%)** for suggestions that pass validation. Routing: rejected → `system_rejected`; flagged or below threshold → `mandatory`; otherwise `standard`.

## Data model

| Table | Purpose |
|---|---|
| `users`, `api_keys` | identities; API keys stored as SHA-256 hashes |
| `documents` | metadata, status, route, **encrypted text**, pointer to the encrypted original |
| `document_sections` | detected sections with offsets |
| `clinical_entities` | entities with assertions, modifiers, source, conflicts (text encrypted) |
| `model_runs` | per stage: provider, model, prompt/retrieval/KB/pipeline versions, config, tokens, cost, latency, raw response (encrypted) |
| `coding_suggestions` | code, description, KB version, confidence + breakdown, retrieval rank, validation status + issues, route, review status, final code |
| `coding_evidence` | evidence quotes with offsets and match score (encrypted) |
| `reviews` | every coder action with original vs. final code, reason and error category |
| `final_codes` | the approved, sequenced code set per document |
| `audit_logs` | append-only hash chain (DB trigger forbids UPDATE/DELETE) |
| `jobs` | durable job queue / workflow runs |
| `kb_versions`, `code_references` | versioned knowledge base with instructional notes |

## Scaling

* **API:** stateless; scale replicas behind the proxy. Each process caches the active KB snapshot (~150 MB for ICD-10-CM).
* **Workers:** `WORKER_REPLICAS` × `WORKER_CONCURRENCY`; jobs are claimed with `FOR UPDATE SKIP LOCKED` and reclaimed after `JOB_LOCK_TIMEOUT_S` if a worker dies.
* **Qdrant:** a single node handles the ~75k-point ICD-10-CM collection comfortably. Use a cluster when you add many code systems or tenants.
* **LLM:** latency and cost dominate. Use the async path (`?async=true` or `process=true` on upload) for batch workloads.

## Extension points

| Need | Where |
|---|---|
| OCR for scanned documents | implement `OcrProvider` in `app/ingestion/extractors.py` (Azure Document Intelligence, AWS Textract, Google Document AI) |
| Object storage (S3/Azure Blob) | replace `LocalEncryptedStorage` in `app/ingestion/storage.py` (keep client-side encryption, add SSE-KMS) |
| New code system | loader in `app/knowledge/other_systems.py`, add it to `ENABLED_CODE_SYSTEMS`, index it |
| New validation rule | method on `ValidationEngine`, plus a unit test in `tests/test_knowledge_validation.py` |
| Different LLM | any OpenRouter model via `LLM_MODEL`, or implement the `CodingModel` protocol |
| medSpaCy / scispaCy | install the `nlp` extra and add an extractor that returns `Entity` objects; ConText reconciliation applies unchanged |
