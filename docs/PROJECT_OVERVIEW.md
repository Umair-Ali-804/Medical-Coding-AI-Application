# Medical Coding AI: pre-bill coding and claim-integrity assistant

## 1. The real-world problem

A provider only gets paid when a claim carries the **right codes, in the right order, with the right units and modifiers, valid on the date of service**. Today, that work is done by coders reading every note and billers fixing claims after they bounce:

* **Denials and rework.** A claim with a header code (`E11`), 3 units where CMS allows 2 (MUE), an E/M visit billed with a procedure but no modifier 25, or a code that was terminated last quarter gets denied (CARC 146, 151, 97, 181...). Each denial costs staff time to work and appeal, and some are never recovered.
* **Coder capacity.** Coders spend much of their time finding the right code and checking rules. Backlogs delay billing (higher days in A/R).
* **Missed revenue.** Procedures documented in the note but never coded (a joint injection, the drug units, a vaccine administration) are simply not billed.
* **Compliance risk.** Coding a *negated*, *family-history* or *"rule out"* diagnosis, or upcoding, creates audit exposure. Every code must be traceable to documentation and guidelines.
* **Code-set churn.** ICD-10-CM/PCS change every October 1, HCPCS/MUE/NCCI every quarter. Billing with expired reference data causes silent denials. (Your uploaded files all expire **2026-09-30**.)

## 2. The solution

**Clinical note in -> reviewed, guideline-cited codes -> scrubbed claim out.** The AI proposes; a certified coder decides.

```
 Clinical note (PDF/DOCX/TXT)
   -> clinical NLP: entities + negation/uncertainty/family/history (ConText) [+ LLM extraction]
   -> hybrid retrieval (BM25 + BGE embeddings) over official code sets
        diagnoses:  ICD-10-CM                  procedures: CPT + HCPCS (outpatient) | ICD-10-PCS (inpatient)
   -> coder model (LLM via OpenRouter, strict JSON; or deterministic retrieval-only coder)
   -> deterministic validation (exists/billable per CMS file, verbatim evidence, negation, Excludes1,
      laterality, sex/age, sequencing, partial-CPT)       -> official guideline citations per code
   -> calibrated confidence + routing (standard / mandatory review / system-rejected)
   -> coder review (approve / edit / reject / add) -> final codes
   -> draft claim (dx pointers, units inferred from dose, e.g. dexamethasone 8 mg -> J1100 x 8)
   -> PRE-BILL CLAIM SCRUBBER: MUE, NCCI PTP, modifiers, E/M + procedure (25), vaccine admin, HCPCS coverage/
      termination, PCS vs setting, Dx validity/sex/age/sequencing/Excludes1, reference freshness
      -> each finding: severity, typical CARC/RARC, fix, source, $ at risk
```

### Who uses it
| User | What they get |
|---|---|
| Coder | Pre-filled codes with highlighted evidence, guideline citation, validation flags; one-click approve/edit |
| Billing / RCM | Claim risk score, denial reason codes and fixes *before* submission; $ at risk |
| Compliance / auditor | Hash-chained audit log; every code tied to evidence, model, prompt, KB version |
| Revenue-integrity manager | Error taxonomy, auto-accept precision, reference-data freshness |

## 3. What was added in this version (on top of the ICD-10-CM platform)

| Area | Added |
|---|---|
| Your data | Loaders for the exact CMS formats you uploaded: ICD-10-CM order/codes files, ICD-10-PCS, HCPCS ANWEB fixed-width (+ modifiers, coverage codes, termination dates, BETOS, processing notes), MUE, processing notes, CPT CSV (dedupe + ranges), guideline PDFs; `kb load-all` auto-detects files; effective-date windows on every set |
| Data quality | CMS order file is the authority for validity (76 invalid tabular 7th-character codes caught); CPT duplicates/ranges handled; `kb freshness` |
| Procedures | CPT/HCPCS (outpatient) and ICD-10-PCS (inpatient) suggestions for procedures documented as *performed* (planned/historical/negated excluded), with vocabulary expansion (laparoscopic cholecystectomy -> PCS "Resection of Gallbladder, Percutaneous Endoscopic Approach") |
| Guidelines RAG | 276 ICD-10-CM + 67 PCS guideline passages, cited per code (e.g. `I.C.4.a` for diabetes/Z79.84) and sent to the LLM as grounding |
| Claim scrubber | 30+ deterministic rules with CARC/RARC mapping (CO-151 MUE and CO-236 NCCI confirmed against Medicare contractor guidance), fixes, sources, risk score, $ at risk; API `POST /api/v1/claims/scrub`, `GET /documents/{id}/claim-check` |
| Bug fixes | Manifestation codes worded "in *other* diseases classified elsewhere" (e.g. F02.80) were not detected; "with obstruction" codes were chosen without documentation (K80.01 -> K80.00); cholelithiasis synonyms |
| Colab | SQLite + disk vector store on Drive, GPU sentence-transformers embeddings, Gradio workbench (5 tabs), notebook |
| Evaluation | Claim-scrubber labeled set + runner; diagnosis accuracy re-checked on all splits (no regression) |

## 4. Results so far (synthetic data, retrieval-only coder, no LLM)

| Measure | Result | Caveat |
|---|---|---|
| ICD-10-CM F1, held-out test split | 0.94 (precision 0.92, recall 0.96, exact-match 0.92) | 12 synthetic notes, lexical-only retrieval; not representative of real documentation. Re-measure with BGE embeddings on Colab |
| ICD-10-CM F1, validation split | 0.88 | synthetic |
| Claim scrubber detection | 22/22 expected findings, 0 false alarms on 4 clean claims | cases were written alongside the rules: regression tests, not real-world accuracy |
| Procedure suggestions | correct on the 3 sample notes (20610 + J1100 x8; 0FT44ZZ; 90715 + "range 12001-12007 needs manual selection") | no labeled procedure data yet; always routed to mandatory review |

**Real accuracy must be measured on your own de-identified, coded encounters and denial history** (see DATA_SOURCES.md, P1).

## 5. KPIs to track in a pilot

| KPI | Target direction | How measured here |
|---|---|---|
| Clean-claim rate (claims with no deny/review findings) | up | scrubber status on submitted claims |
| Initial denial rate, by CARC | down | compare scrubber findings with 835 remits |
| Coder minutes per chart | down | review timestamps (`reviews` table) |
| Auto-accept precision (codes accepted unchanged) | >= 95% before any auto-accept | `app.evaluation.runner --calibrate` |
| Missed-charge capture ($) | up | procedure suggestions accepted that were not on the original claim |
| Reference data freshness | 100% valid on DOS | `kb freshness` |

## 6. Roadmap

1. **Now (Colab pilot):** load FY2027 + Q4 2026 files, add NCCI PTP, run on 100-300 de-identified charts per specialty, measure against final coded claims.
2. **Next:** show guideline citations and the claim check in the Next.js review UI (today they are in the API and the Gradio workbench); full CPT license, LCD/NCD coverage crosswalk, fee schedule; E/M level support from MDM/time documentation; multi-version validation by date of service; train/tune on your coder corrections (the `reviews` table is the training signal).
3. **Production:** Docker stack (Postgres, Qdrant, API, workers, Next.js review UI, n8n intake/export), SSO, BAA with every processor (LLM provider included), security review, payer-specific rules, EHR/clearinghouse integration.

## 7. Guardrails (non-negotiable)

* Decision support only: **a certified coder approves every code**; integrations cannot approve.
* The LLM never has the last word: deterministic validation + platform-computed confidence.
* Only synthetic/de-identified data outside a HIPAA-compliant environment (Colab is not one).
* CPT is AMA-licensed content: load only under a valid license.
