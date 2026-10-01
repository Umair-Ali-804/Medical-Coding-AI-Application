# Data: what you have, what is missing, where to get it

Put reference files in `data_sources/` (keep the official file names) and run
`python -m app.cli kb load-all --data-dir data_sources` - files are recognized by name.

## 1. What you have (loaded and used)

| File | What it is | Rows loaded | Valid for dates of service | Used for |
|---|---|---|---|---|
| `icd10cm_order_2026.txt` | CMS ICD-10-CM FY2026 order file (codes + headers, billable flag) | 98,186 (74,719 billable) | 2025-10-01 to 2026-09-30 | **Authority for code validity.** Cross-checked against the CDC tabular XML: 76 tabular 7th-character expansions (e.g. `S06.1X7D`) are *not* valid CMS codes and are marked non-billable |
| `icd10cm_codes_2026.txt` | Billable codes only | 74,719 | same | Fallback if the order file is absent |
| (bundled) CDC tabular XML via `simple-icd-10-cm` | Excludes1/2, Use additional code, Code first, inclusion terms | 98,262 | FY2026 | Validation rules (Excludes1, manifestation/etiology, use-additional-code) |
| `icd10pcs_codes_2026.txt` | ICD-10-PCS FY2026 | 79,193 | 2025-10-01 to 2026-09-30 | Inpatient procedure suggestions |
| `HCPC2026_JUL_ANWEB_06172026.txt` | HCPCS Level II, July 2026 quarter (fixed width) | 8,725 codes (7,418 active) + 384 modifiers | 2026-07-01 to 2026-09-30 | Drugs/supplies/G-codes, coverage codes, termination dates, BETOS, processing notes, modifier validation |
| `proc_notes_JUL2026.txt` | HCPCS processing notes | 226 active notes | Q3 2026 | Shown to coders on claim lines |
| `MCD-MUE-PractitionerServices-Effective_07012026.txt` | CMS NCCI MUE, practitioner services | 15,331 unit limits | 2026-07-01 to 2026-09-30 | Unit-of-service denials (CARC 151) |
| `cpt_codes.csv` | Your CPT extract | 274 codes (3 duplicate rows merged, 10 range rows kept as guidance) | 2026 | CPT suggestions + validation (only with `--i-have-a-cpt-license`) |
| `coding_guidlines.pdf` | ICD-10-CM Official Guidelines FY2026 | 276 passages | FY2026 | Citations per code + LLM grounding |
| `pcs_guidelines_2026.pdf` | ICD-10-PCS Official Guidelines 2026 | 67 passages | FY2026 | PCS citations |
| `data/evaluation/*.jsonl` | 36 synthetic labeled notes | 71 gold ICD-10-CM codes | - | Diagnosis-coding accuracy |
| `data/evaluation/claims_scrub_cases.jsonl` | 26 labeled claims | 22 expected findings | - | Claim-scrubber regression tests |

### Data-quality findings in your files
* **Everything expires on 2026-09-30.** ICD-10-CM/PCS FY2026, HCPCS July quarter and the Q3 MUE file are all invalid for dates of service from **2026-10-01**. `kb freshness` and the claim scrubber flag this. Download the FY2027 / October 2026 releases (links below).
* The CDC tabular 7th-character expansion creates 76 codes that are not in the CMS code file (now rejected as "not a valid code in the CMS code file").
* `cpt_codes.csv`: 3 duplicated codes (27447, 29881, 31575) and 10 range rows (e.g. `12001-12007`) that are not reportable codes. Only 274 of ~11,000 CPT codes are present, so most CPT codes cannot be validated.
* The MUE file has no MUE Adjudication Indicator column (line vs. date-of-service edits). The scrubber checks per line and also warns when units summed across lines exceed the limit.

## 2. What is missing, and where to get it

Priority: **P1** = needed for real-world accuracy, **P2** = strongly recommended, **P3** = nice to have.

| # | Missing data | Why it matters | Where to get it | Cost / access | Loader |
|---|---|---|---|---|---|
| P1 | **FY2027 ICD-10-CM + ICD-10-PCS** (code files, order files, tabular XML, guidelines) | Your FY2026 files are not valid for DOS >= 2026-10-01 | CMS: https://www.cms.gov/medicare/coding-billing/icd-10-codes (FY 2027 section). CDC: https://www.cdc.gov/nchs/icd/icd-10-cm/files.html | Free | `kb load-all` (same file names with 2027) |
| P1 | **Current HCPCS quarterly file** (Oct 2026) | New/terminated drug and supply codes every quarter | https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update | Free | `HCPC2026_OCT_ANWEB*.txt` |
| P1 | **Current MUE files** (practitioner, outpatient hospital, DME) | Q3 MUE ends 2026-09-30; you only have the practitioner table | https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medically-unlikely-edits | Free | any `*MUE*` file |
| P1 | **NCCI Procedure-to-Procedure (PTP) edits** | Bundling denials (CARC 236) are a frequent cause of coding denials; without PTP the scrubber only applies the E/M + procedure/modifier-25 rule | https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits (practitioner `ccipra*`, hospital `ccioph*`) | Free | `kb load-file <file> --kind ptp` |
| P1 | **Full CPT code set** | Only 274 codes today; E/M, surgery, radiology, lab lookups and validation need the full set | AMA CPT data file license: https://www.ama-assn.org/practice-management/cpt/cpt-licensing (or a licensed distributor / your EHR vendor) | Paid license | `cpt*.csv` + `--i-have-a-cpt-license` |
| P1 | **Real, de-identified clinical notes with final coded claims** from your organization | The single most important dataset: accuracy must be measured on your specialties, templates and payers | Your EHR + billing system (final coded encounters), de-identified under a BAA/DUA | Internal | `data/evaluation/*.jsonl` format (see `data/README.md`) |
| P1 | **Denial / remittance history (835 ERA)** with CARC/RARC | Tells you which denials actually happen, lets you tune scrubber rules and measure $ recovered | Your clearinghouse / payer 835 files; code lists: https://x12.org/codes | Internal (code lists free to view) | add as claims in `claims_scrub_cases.jsonl` |
| P2 | **LCD/NCD medical-necessity crosswalks** (ICD-10 codes that support each CPT/HCPCS) | Medical-necessity denials (CARC 50) | CMS Medicare Coverage Database downloads: https://www.cms.gov/medicare-coverage-database/downloads/downloads.aspx (join `article_x_hcpc_code` with `article_x_icd10_covered` by article id + group) | Free | `coverage*.csv` with columns `procedure_code,icd10_code` |
| P2 | **Fee schedule** (Physician Fee Schedule RVUs + GPCI, or your contract rates) | Turns risk into dollars at risk per claim | https://www.cms.gov/medicare/payment/fee-schedules/physician (PFS look-up tool / RVU files) | Free (contracts internal) | `fee*.csv` with columns `code,amount` |
| P2 | **ICD-10-CM Index and Table of Drugs/Neoplasms** | Better retrieval for index-driven coding (e.g. "cholelithiasis with acute cholecystitis -> K80.00") | Same CMS/CDC FY2027 pages (index XML) | Free | future loader (retrieval synonyms) |
| P2 | **ICD-10-PCS tables & definitions XML** (body parts, root operations, approaches) | Better PCS construction than whole-code search | CMS ICD-10 page (PCS files) | Free | future loader |
| P2 | **MS-DRG grouper definitions / Medicare Code Editor (MCE)** | Inpatient DRG impact, complete sex/age/manifestation edits (the scrubber has a subset) | https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/ms-drg-classifications-and-software | Free | future |
| P3 | **Public research datasets for benchmarking** | Larger labeled sets for model development | MIMIC-IV + MIMIC-IV-Note (ICD-10 coded discharge summaries): https://physionet.org/content/mimiciv/ and https://physionet.org/content/mimic-iv-note/ ; MDACE (code evidence spans on MIMIC-III): https://github.com/3mcloud/MDACE | Free; PhysioNet credentialing + CITI training + DUA. **Not allowed to send to third-party LLM APIs under the DUA without approval.** | convert to `data/evaluation` JSONL |
| P3 | Place of service, revenue codes, NDC-HCPCS crosswalk, OPPS status indicators | Institutional claim edits, drug unit conversion | CMS POS list; NUBC (revenue codes, licensed); CMS ASP NDC-HCPCS crosswalk; OPPS Addendum B | Mixed | future |
| P3 | Synthetic patients for load/demo testing | Volume tests without PHI | Synthea: https://synthea.mitre.org | Free | - |

## 3. How to keep data current (real-world operating rhythm)

| When | What to reload |
|---|---|
| Every **October 1** (and April 1 updates) | ICD-10-CM, ICD-10-PCS, Official Guidelines |
| Every **quarter** (Jan/Apr/Jul/Oct) | HCPCS, MUE, NCCI PTP, processing notes |
| Every **January** | CPT (licensed), fee schedule |
| Continuously | LCD/NCD articles, payer policies, your denial data |

Previous versions stay in the database (`kb_versions`) for audit, and every suggestion records the code-set version it was made with. The claim scrubber validates against the *active* release and warns when the date of service falls outside that release's validity window (for example, a claim dated 2026-10-05 checked against FY2026 files). Routing each claim to the release in effect on its date of service is on the roadmap.
