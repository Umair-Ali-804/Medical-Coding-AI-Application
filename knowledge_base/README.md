# Knowledge base sources

The platform loads coding references into PostgreSQL (`code_references`, versioned in `kb_versions`) and embeds billable codes into Qdrant. Nothing in this folder is required at runtime; drop source files here to load them with the CLI.

| Folder | Code set | Source | Load |
|---|---|---|---|
| `icd10cm/` | ICD-10-CM (FY, effective Oct 1) | CDC/NCHS `icd10cm-tabular-<YEAR>.xml` (public domain). Bundled via the `simple-icd-10-cm` package, so no download is needed | `python -m app.cli kb load-icd10cm [--xml icd10cm/icd10cm-tabular-2026.xml]` |
| `hcpcs/` | HCPCS Level II | CMS quarterly update (`HCPC<YEAR>_<MON>_ANWEB.xlsx`) | `python -m app.cli kb load-hcpcs hcpcs/HCPC2026_JAN_ANWEB.xlsx --version 2026Q1` |
| `cpt/` | CPT® | **AMA license required.** Never commit CPT files | `python -m app.cli kb load-cpt cpt/licensed.csv --version 2026 --i-have-a-cpt-license` |
| `guidelines/` | ICD-10-CM Official Guidelines (PDF) | CMS/NCHS | reference for reviewers; key rules are encoded in `app/validation` and `app/llm/prompts.py` |

After loading: `python -m app.cli kb index --system <SYSTEM>` and add the system to `ENABLED_CODE_SYSTEMS`.
