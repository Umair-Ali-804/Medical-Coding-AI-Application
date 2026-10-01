# data_sources

Official reference files, loaded with `python -m app.cli kb load-all --data-dir data_sources`
(files are recognized by their official names). See `docs/DATA_SOURCES.md` for what each file is,
what is still missing and where to download current releases.

Current contents are the FY2026 / Q3-2026 releases: **valid for dates of service up to 2026-09-30.**
Add FY2027 ICD-10-CM/PCS and the October 2026 HCPCS / MUE / NCCI files for later dates of service.

`cpt_codes.csv` contains CPT content (AMA copyright): keep it out of public repositories and load it only
under a valid CPT license (`--i-have-a-cpt-license`).
