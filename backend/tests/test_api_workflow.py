"""End-to-end API tests: upload -> extract -> code -> review -> finalize -> export -> audit."""

from __future__ import annotations

import io
import json

from sqlalchemy import text

from tests.conftest import DATA_DIR

NOTE = json.loads((DATA_DIR / "dev.jsonl").read_text().splitlines()[0])  # DOC001: E11.9, I10, Z79.84


def _upload_text(client, headers, body_text=None, **extra):
    r = client.post(
        "/api/v1/documents/text",
        headers=headers,
        json={
            "text": body_text or NOTE["clinical_text"],
            "filename": "doc001.txt",
            "patient_sex": "M",
            "patient_age": 58,
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["document_id"]


def test_health(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["checks"]["knowledge_base"] == "ok"


def test_auth_required_and_bad_login(client):
    assert client.get("/api/v1/documents").status_code == 401
    r = client.post(
        "/api/v1/auth/login", data={"username": "coder@example.com", "password": "wrong-password-1A"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_full_review_workflow(client, coder_headers, auditor_headers, admin_headers):
    doc_id = _upload_text(client, coder_headers)

    r = client.post(f"/api/v1/documents/{doc_id}/extract", headers=coder_headers)
    assert r.status_code == 200 and r.json()["status"] == "entities_extracted"
    detail = client.get(f"/api/v1/documents/{doc_id}", headers=coder_headers).json()
    ents = {e["text"].lower(): e for e in detail["entities"]}
    assert ents["chest pain"]["negated"] is True
    assert ents["diabetic complications"]["negated"] is True

    r = client.post(f"/api/v1/documents/{doc_id}/code", headers=coder_headers)
    assert r.status_code == 200, r.text
    assert r.json()["suggestions"] >= 3

    sugg = client.get(f"/api/v1/documents/{doc_id}/suggestions", headers=coder_headers).json()
    codes = {s["code"]: s for s in sugg}
    assert {"E11.9", "I10", "Z79.84"} <= set(codes), codes.keys()
    e119 = codes["E11.9"]
    assert e119["validation_status"] in ("passed", "flagged")
    assert e119["evidence"] and e119["evidence"][0]["match_score"] >= 0.97
    assert set(e119["confidence_breakdown"]["signals"]) >= {"evidence", "retrieval", "validation", "entity"}

    # auditors cannot review
    r = client.post(f"/api/v1/suggestions/{e119['id']}/approve", headers=auditor_headers)
    assert r.status_code == 403

    # cannot finalize with pending suggestions
    assert client.post(f"/api/v1/documents/{doc_id}/finalize", headers=coder_headers).status_code == 409

    assert (
        client.post(f"/api/v1/suggestions/{e119['id']}/approve", headers=coder_headers).json()["status"]
        == "approved"
    )
    # edit I10 to an invalid code -> 422; to a valid one -> edited
    i10 = codes["I10"]
    bad = client.post(
        f"/api/v1/suggestions/{i10['id']}/edit",
        headers=coder_headers,
        json={"code": "I10.9", "reason": "test"},
    )
    assert bad.status_code == 422
    r = client.post(
        f"/api/v1/suggestions/{i10['id']}/edit",
        headers=coder_headers,
        json={
            "code": "I11.9",
            "reason": "documentation supports hypertensive heart disease",
            "error_category": "wrong_code",
        },
    )
    assert r.json()["status"] == "edited" and r.json()["final_code"] == "I11.9"
    for code, s in codes.items():
        if code not in ("E11.9", "I10"):
            client.post(
                f"/api/v1/suggestions/{s['id']}/reject",
                headers=coder_headers,
                json={"reason": "not supported", "error_category": "extra_code"},
            )
    # coder adds a missed code
    r = client.post(
        f"/api/v1/documents/{doc_id}/suggestions",
        headers=coder_headers,
        json={"code": "Z79.899", "reason": "on lisinopril long term", "evidence": "Lisinopril 20 mg daily"},
    )
    assert r.status_code == 201 and r.json()["source"] == "manual"

    r = client.post(f"/api/v1/documents/{doc_id}/finalize", headers=coder_headers)
    assert r.status_code == 200, r.text
    final = [c["code"] for c in r.json()]
    assert final == ["E11.9", "I11.9", "Z79.899"] or set(final) == {"E11.9", "I11.9", "Z79.899"}

    # finalized documents are locked
    assert (
        client.post(
            f"/api/v1/suggestions/{e119['id']}/reject", headers=coder_headers, json={"reason": "late change"}
        ).status_code
        == 409
    )

    csv_resp = client.get(f"/api/v1/documents/{doc_id}/export?format=csv", headers=coder_headers)
    assert csv_resp.status_code == 200 and "E11.9" in csv_resp.text
    js = client.get(f"/api/v1/documents/{doc_id}/export", headers=coder_headers).json()
    assert {c["code"] for c in js["codes"]} == {"E11.9", "I11.9", "Z79.899"}

    audit = client.get(f"/api/v1/documents/{doc_id}/audit", headers=auditor_headers).json()
    actions = [a["action"] for a in audit]
    for a in (
        "document.uploaded",
        "document.extracted",
        "document.coded",
        "suggestion.approved",
        "suggestion.edited",
        "suggestion.added",
        "document.finalized",
        "document.exported",
    ):
        assert a in actions
    assert all("clinical" not in json.dumps(a["details"]).lower() for a in audit)

    runs = client.get(f"/api/v1/documents/{doc_id}/runs", headers=coder_headers).json()
    coding = [r for r in runs if r["run_type"] == "coding"][-1]
    assert coding["kb_version"] == "2026" and coding["retrieval_version"] and coding["prompt_version"]

    verify = client.get("/api/v1/audit/verify", headers=admin_headers).json()
    assert verify["valid"] is True and verify["checked"] > 10

    stats = client.get("/api/v1/stats", headers=coder_headers).json()
    assert stats["reviews_by_action"]["approve"] >= 1 and stats["error_categories"].get("wrong_code", 0) >= 1


def test_phi_encrypted_at_rest(client, coder_headers, db):
    doc_id = _upload_text(client, coder_headers)
    raw = db.execute(
        text("SELECT text FROM documents WHERE id = :i"), {"i": doc_id.replace("-", "")}
    ).scalar()
    if raw is None:  # SQLite stores UUIDs as 32-char hex; fall back to scanning
        raw = db.execute(text("SELECT text FROM documents ORDER BY created_at DESC LIMIT 1")).scalar()
    assert raw and "diabetes" not in raw.lower() and raw.startswith("gAAAA")


def test_docx_and_pdf_upload(client, coder_headers):
    import docx

    d = docx.Document()
    for line in NOTE["clinical_text"].splitlines():
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    r = client.post(
        "/api/v1/documents",
        headers=coder_headers,
        files={"file": ("note.docx", buf.getvalue(), "application/octet-stream")},
    )
    assert r.status_code == 201, r.text

    pdf = _minimal_pdf("Assessment: Essential hypertension. Patient denies chest pain today.")
    r = client.post(
        "/api/v1/documents",
        headers=coder_headers,
        files={"file": ("note.pdf", pdf, "application/pdf")},
        data={"process": "false"},
    )
    assert r.status_code == 201, r.text
    detail = client.get(f"/api/v1/documents/{r.json()['document_id']}", headers=coder_headers).json()
    assert "hypertension" in detail["text"].lower() and detail["mime_type"] == "application/pdf"


def test_rejects_unsupported_and_empty(client, coder_headers):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    r = client.post("/api/v1/documents", headers=coder_headers, files={"file": ("x.png", png, "image/png")})
    assert r.status_code == 415
    r = client.post(
        "/api/v1/documents", headers=coder_headers, files={"file": ("x.txt", b"hi", "text/plain")}
    )
    assert r.status_code == 422


def test_api_key_service_role(client, admin_headers):
    r = client.post("/api/v1/api-keys", headers=admin_headers, json={"name": "n8n", "role": "service"})
    assert r.status_code == 201
    key = {"X-API-Key": r.json()["key"]}
    doc_id = _upload_text(client, key)
    r = client.post(f"/api/v1/documents/{doc_id}/process", headers=key)
    assert r.status_code == 200 and r.json()["review_route"] in ("standard", "mandatory")
    sid = client.get(f"/api/v1/documents/{doc_id}/suggestions", headers=key).json()[0]["id"]
    # integrations can never approve codes
    assert client.post(f"/api/v1/suggestions/{sid}/approve", headers=key).status_code == 403
    # revoked keys stop working
    kid = client.get("/api/v1/api-keys", headers=admin_headers).json()[0]["id"]
    client.delete(f"/api/v1/api-keys/{kid}", headers=admin_headers)
    assert client.get("/api/v1/documents", headers=key).status_code == 401


def test_async_processing_with_worker(client, coder_headers):
    from app.worker import process_one

    doc_id = _upload_text(client, coder_headers, process=True)
    r = client.get(f"/api/v1/documents/{doc_id}", headers=coder_headers).json()
    assert r["status"] == "processing"
    assert process_one("test-worker") is True
    r = client.get(f"/api/v1/documents/{doc_id}", headers=coder_headers).json()
    assert r["status"] == "coded" and r["review_route"] in ("standard", "mandatory")


def test_callback_url_must_be_allowlisted(client, coder_headers):
    r = client.post(
        "/api/v1/documents/text",
        headers=coder_headers,
        json={"text": NOTE["clinical_text"], "process": True, "callback_url": "http://169.254.169.254/x"},
    )
    assert r.status_code == 422


def test_user_admin(client, admin_headers, coder_headers):
    r = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"email": "new.coder@example.com", "password": "weak", "role": "coder"},
    )
    assert r.status_code == 422
    r = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"email": "new.coder@example.com", "password": "Str0ng-Password!", "role": "coder"},
    )
    assert r.status_code == 201
    assert client.get("/api/v1/users", headers=coder_headers).status_code == 403


def test_knowledge_endpoints(client, coder_headers):
    r = client.get("/api/v1/knowledge/codes/ICD-10-CM/e119", headers=coder_headers).json()
    assert r["code"] == "E11.9" and r["inherited_excludes1"]
    hits = client.get("/api/v1/knowledge/search?q=essential hypertension", headers=coder_headers).json()
    assert hits[0]["code"] == "I10"


def _minimal_pdf(line: str) -> bytes:
    """Hand-built single-page PDF with a text layer."""
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out
