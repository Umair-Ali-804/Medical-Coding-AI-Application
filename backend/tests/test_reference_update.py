"""Annual code-set update: the newest release and a local CDC tabular XML must be picked up."""

from __future__ import annotations

from app.knowledge.cms_loaders import detect_files


def test_detects_tabular_and_all_releases(tmp_path):
    for name in (
        "icd10cm_order_2026.txt",
        "icd10cm_order_2027.txt",
        "icd10cm-tabular-2027.xml",
        "icd10pcs_codes_2026.txt",
        "icd10pcs_codes_2027.txt",
    ):
        (tmp_path / name).write_text("x")
    kinds = {f.path.name: f.kind for f in detect_files(tmp_path)}
    assert kinds["icd10cm-tabular-2027.xml"] == "icd10cm_tabular"
    assert kinds["icd10cm_order_2027.txt"] == kinds["icd10cm_order_2026.txt"] == "icd10cm_order"
    newest = sorted(n for n, k in kinds.items() if k == "icd10cm_order")[-1]
    assert newest == "icd10cm_order_2027.txt"  # load_files loads the newest release, not the first one


def test_expired_code_set_forces_mandatory_review(kb, db, monkeypatch):
    """With an expired code set (e.g. FY2026 after 2026-09-30) no chart may be routed as 'standard'."""
    from app.models import ModelRun
    from app.services import pipeline
    from app.services.principal import SYSTEM

    warning = "ICD-10-CM 2026 expired on 2026-09-30 (date checked: 2026-10-01)."
    monkeypatch.setattr(pipeline, "_reference_warnings", lambda _db: [warning])
    note = "Assessment:\n1. Essential hypertension, well controlled\n2. Hyperlipidemia"
    doc = pipeline.ingest_document(
        db, SYSTEM, data=note.encode(), filename="exp.txt", patient_sex="M", patient_age=60
    )
    db.commit()
    summary = pipeline.process_document(db, doc.id, SYSTEM)
    db.commit()
    assert summary.route == "mandatory"
    run = next(r for r in db.query(ModelRun).filter_by(document_id=doc.id) if r.run_type == "coding")
    assert run.config["reference_data_warnings"] == [warning]
    assert "guideline_rule_changes" in run.config
