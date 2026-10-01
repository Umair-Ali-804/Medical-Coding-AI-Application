# Data

| Folder | Contents |
|---|---|
| `raw/` | synthetic source notes (`DOC001.txt` to `DOC036.txt`), handy for demos and manual uploads |
| `cleaned/`, `processed/` | scratch space for your own pipelines (the platform stores documents in Postgres and encrypted storage, not here) |
| `evaluation/` | `dev.jsonl`, `validation.jsonl`, `test.jsonl`: labeled splits for `app.evaluation.runner` |

Everything here is **synthetic**. Never place real patient records in this repository. For real evaluation data, use de-identified documents under an appropriate data use agreement and keep them outside version control.

Regenerate: `python scripts/build_synthetic_dataset.py`.
