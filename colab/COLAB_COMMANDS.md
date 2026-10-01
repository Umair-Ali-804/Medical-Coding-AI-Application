# Run on Google Colab

1. Upload `medical-coding-ai.zip` to the top level of **My Drive**.
2. In Colab: **File -> Upload notebook -> `colab/MedicalCodingAI_Colab.ipynb`** (it is inside the zip, and also delivered separately), or paste the cells below into a new notebook.
3. **Runtime -> Change runtime type -> T4 GPU** (optional, speeds up the one-time embedding step).
4. Optional LLM: Colab **Secrets** (key icon) -> add `OPENROUTER_API_KEY`. Without it, the deterministic retrieval-only coder is used.

## Cells

```python
# 1. Mount Drive + unzip
from google.colab import drive
drive.mount('/content/drive')
ZIP = '/content/drive/MyDrive/medical-coding-ai.zip'
DRIVE_WORK = '/content/drive/MyDrive/medical-coding-ai-work'
!rm -rf /content/medical-coding-ai && unzip -q -o "$ZIP" -d /content/
!mkdir -p "$DRIVE_WORK" /content/runtime
```

```python
# 2. Install
%cd /content/medical-coding-ai/backend
!pip install -q -e ".[kb]" gradio
```

```python
# 3. Configure
import os, json, secrets, base64
from pathlib import Path
keys_file = Path(DRIVE_WORK) / 'secrets.json'
if not keys_file.exists():
    keys_file.write_text(json.dumps({'JWT_SECRET': secrets.token_urlsafe(48),
        'ENCRYPTION_KEYS': base64.urlsafe_b64encode(os.urandom(32)).decode(),
        'ADMIN_PASSWORD': 'Adm1n-' + secrets.token_urlsafe(9)}))
keys = json.loads(keys_file.read_text())
os.environ.update({
    'ENVIRONMENT': 'development',
    'DATABASE_URL': 'sqlite:////content/runtime/medcoding.db',
    'STORAGE_DIR': '/content/runtime/storage',
    'VECTOR_STORE': 'disk', 'VECTOR_DIR': f'{DRIVE_WORK}/vectors',
    'EMBEDDING_PROVIDER': 'sentence_transformers', 'EMBEDDING_MODEL': 'BAAI/bge-small-en-v1.5',
    'ENABLED_CODE_SYSTEMS': 'ICD-10-CM,ICD-10-PCS,HCPCS,CPT',
    'LLM_PROVIDER': 'heuristic', 'LOG_JSON': 'false', 'LOG_LEVEL': 'WARNING',
    'JWT_SECRET': keys['JWT_SECRET'], 'ENCRYPTION_KEYS': keys['ENCRYPTION_KEYS'], 'CORS_ORIGINS': '*',
})
try:
    from google.colab import userdata
    k = userdata.get('OPENROUTER_API_KEY')
    if k: os.environ.update({'LLM_PROVIDER': 'openrouter', 'OPENROUTER_API_KEY': k})
except Exception:
    pass
```

```bash
# 4. Load your reference data (drop --i-have-a-cpt-license if you are not CPT-licensed)
!python -m app.cli init-db
!python -m app.cli kb load-all --data-dir /content/medical-coding-ai/data_sources --i-have-a-cpt-license
!python -m app.cli kb status

# 5. Semantic index (once; stored on Drive and reused next session)
!python -m app.cli kb index --system all

# 6. Is the reference data valid for the date of service?
!python -m app.cli kb freshness --dos 2026-10-01

# 7. Tests (optional)
!python -m pytest -q -p no:warnings

# 8. Code a note end to end
!python -m app.cli code-note ../data/sample_notes/01_office_visit_knee_injection.txt --sex M --age 67 --dos 2026-09-15
!python -m app.cli code-note ../data/sample_notes/02_inpatient_lap_chole.txt --encounter inpatient --sex F --age 52 --dos 2026-09-10

# 9. Claim scrubber evaluation / your own claim JSON
!python -m app.evaluation.claims_eval
!python -m app.cli claims scrub /content/runtime/claim.json

# 10. Diagnosis-coding accuracy
!python -m app.evaluation.runner --split test --baselines retrieval_only

# 11. Workbench UI (prints a public https://xxxx.gradio.live link)
!python -m app.demo.gradio_app --share
```

```python
# 12. Optional: REST API + Swagger
!python -m app.cli create-user admin@example.org --role admin --password "{keys['ADMIN_PASSWORD']}"
!nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 > /content/runtime/api.log 2>&1 &
from google.colab.output import eval_js
print(eval_js("google.colab.kernel.proxyPort(8000)") + 'docs')
```

## Notes
* Each new Colab session: run cells 1-4 again (about 2 minutes). Step 5 is instant after the first time because the vectors are on Drive.
* When you get new files (FY2027 ICD-10, October HCPCS/MUE, NCCI PTP...), copy them into `data_sources/` (or any folder) and rerun `kb load-all --data-dir <folder>` then `kb index --system all`.
* No GPU? Everything still works; the one-time embedding step takes longer. For a quick look without embeddings set `EMBEDDING_PROVIDER=hash` (lexical-quality retrieval).
