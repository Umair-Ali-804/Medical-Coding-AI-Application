# Evaluation

## Dataset

`data/evaluation/{dev,validation,test}.jsonl`, built by `scripts/build_synthetic_dataset.py`. There are 36 **synthetic** clinical notes (no real patients) across primary care, urgent care, ED, specialty consults and inpatient discharge summaries, with 71 gold ICD-10-CM FY2026 codes assigned per the Official Guidelines and verified against the CDC tabular list.

The notes deliberately exercise: negation ("denies", "no evidence of", "ruled out"), uncertainty ("rule out MI"), family history (as Z-codes only when assessed), personal history, combination codes (diabetes with CKD, hypertension with CKD or heart failure), 7th characters (initial encounter), laterality, BMI with a weight diagnosis, long-term drug therapy (Z79), manifestation/"use additional code" conventions, symptoms integral to a diagnosis, and outpatient vs. inpatient uncertainty rules.

| Split | Docs | Gold codes | Use |
|---|---|---|---|
| dev | 12 | 21 | rule/prompt iteration |
| validation | 12 | 25 | calibration and model selection |
| test | 12 | 25 | final reporting only; run once, never tuned on |

Record format:
```json
{"document_id": "DOC001", "clinical_text": "...", "encounter_type": "outpatient",
 "patient_sex": "M", "patient_age": 58, "gold_codes": [{"system": "ICD-10-CM", "code": "E11.9"}]}
```

**The synthetic set is a development harness, not a clinical validation.** Before production, build a de-identified set from your own documentation (a few hundred notes per specialty, double-coded by certified coders, with adjudicated disagreements) and rerun everything below.

## Baselines (as specified in the project plan)

| Baseline | What runs | Mode |
|---|---|---|
| `llm_only` (B1) | LLM reads the note and predicts codes; no retrieval, no validation | `direct` |
| `llm_rag` (B2) | LLM plus retrieval over raw sentences; no NLP assertions, no validation | `rag` |
| `llm_rag_validation` (B3) | B2 plus deterministic validation (rejected codes dropped) | `rag` |
| `full` (B4) | NLP entities + ConText + entity-level RAG + LLM + validation + confidence | `full` |
| `retrieval_only` | full pipeline with the deterministic heuristic coder (no LLM) | `full` |

## Metrics

precision, recall, F1 (exact code); category P/R/F1 (3-character); exact-match document accuracy; **unsupported-code rate** (evidence not found verbatim, or nonexistent code); invalid-code rate; **retrieval recall** (gold code present among retrieved candidates); **auto-accept precision** (precision of suggestions routed to standard review); mandatory-review share; latency p50/p95; cost per document.

Errors are categorized as: `wrong_specificity` (right category, wrong code), `retrieval_failure` (gold code never retrieved), `missing_code` (retrieved but not chosen: a reasoning failure), `extra_code`, `negation_error` (predicted code whose documentation is negated/uncertain/family), and `validation_failure`. The categories point at the component to fix instead of blindly swapping models.

## Results: retrieval-only baseline (no LLM)

Configuration: ICD-10-CM 2026, lexical-only retrieval (the dense model could not be downloaded in the build sandbox), heuristic coder `retrieval-top1-v1`, calibration fitted on validation (`docs/evaluation/calibration-retrieval-only.json`, threshold 0.974 for 95% target precision).

| Split | P | R | F1 | Cat. F1 | Exact match | Unsupported | Retrieval recall | Auto-accept precision | Mandatory share |
|---|---|---|---|---|---|---|---|---|---|
| dev (tuned on) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0% | 100% | 1.00 | 0% |
| validation | 0.88 | 0.88 | 0.88 | 0.96 | 0.83 | 0% | 96% | 0.95 | 12% |
| **test (single frozen run)** | **0.89** | **0.96** | **0.92** | **0.98** | **0.83** | **0%** | **100%** | **0.92** | **7%** |

Latency is about 11 ms per document (p50) without an LLM. Full report: `docs/evaluation/test-report-retrieval-only.json`.

### Error analysis (test split)

| Doc | Error | Component | Action |
|---|---|---|---|
| DOC033 | "Non-smoker" coded F17.210 | NLP: the `non-` prefix wasn't a negation trigger | **Fixed** after the frozen run (`_NON_PREFIX` in ConText, plus a regression test) |
| DOC035 | "Coronary artery disease" → I25.119 (with angina) instead of I25.10 | retrieval ranking among siblings | left to the LLM, which reads the candidates' full titles; tracked |
| DOC035 | extra I21.9 next to I21.4 | concept grouping: the "myocardial infarction" mention wasn't folded into the NSTEMI diagnosis | tracked; validation flags the pair as unspecified-with-specific |

After the non-smoker fix, a re-run on test gives F1 0.94 (`test-report-retrieval-only-postfix.json`). That number is reported for transparency but **is not an unbiased estimate**, because the fix was motivated by a test-set error.

Validation-split errors that remain (sepsis-due-to-UTI sequencing, "colon cancer" → "digestive organs" family history) need clinical reasoning, which is exactly what the LLM stage adds.

## Running the LLM baselines

```bash
export LLM_PROVIDER=openrouter OPENROUTER_API_KEY=sk-or-... LLM_MODEL=anthropic/claude-sonnet-4.5
python -m app.evaluation.runner --split dev --baselines all --out reports/dev.json
python -m app.evaluation.runner --split validation --calibrate reports/calibration.json
CALIBRATION_FILE=reports/calibration.json python -m app.evaluation.runner --split test --out reports/test.json
```

When you compare models or prompts, change one thing at a time, keep the prompt version in `app/llm/prompts.py` in sync (it is stored on every model run), and compare on validation. Touch test only for the final report.

## Calibration

`--calibrate` collects every non-rejected suggestion from the primary baseline with its signals and a correct/incorrect label, then:

1. fits L2-regularized logistic-regression weights (shrunk toward the priors) when there are at least 30 labeled suggestions with both classes present; otherwise it keeps the priors;
2. picks the **lowest threshold at which suggestions passing validation reach the target precision** (default 95%, `--target-precision`), with a minimum support of 10.

It writes `{weights, bias, threshold, metrics}` to JSON. Point `CALIBRATION_FILE` at it. Recalibrate whenever the model, prompt, KB version or document mix changes. In production, coder decisions (`reviews`) become the labeled data for the next calibration.
