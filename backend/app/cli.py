"""Operations CLI: `python -m app.cli <command>`.

init-db                             create tables (SQLite: create_all; Postgres: alembic upgrade head)
gen-secrets                         print fresh JWT_SECRET and ENCRYPTION_KEYS values
create-user EMAIL --role coder      create a user (password prompted or --password)
create-api-key NAME [--role service]
kb load-icd10cm [--xml PATH]        load ICD-10-CM from the official CDC tabular XML
kb load-hcpcs FILE --version V      load HCPCS Level II from CMS file (xlsx/csv)
kb load-cpt FILE --version V --i-have-a-cpt-license
kb load-all --data-dir DIR [--i-have-a-cpt-license]
                                    load every recognized CMS/CDC file in DIR (ICD-10-CM/PCS, HCPCS,
                                    CPT, MUE, NCCI PTP, processing notes, guidelines PDFs, fee, coverage)
kb load-file FILE [--kind K]        load one reference file
kb index [--system ICD-10-CM|all]   embed active KB into the vector store
kb freshness [--dos YYYY-MM-DD]     which reference sets are not valid for a date of service
kb status
claims scrub CLAIM.json             pre-bill claim check (denial risks with CARC codes)
code-note FILE [--encounter ...]    run the full pipeline on one note and print codes + claim check
rotate-keys                         re-encrypt PHI columns with the first ENCRYPTION_KEYS key
verify-audit                        verify the audit hash chain
"""

from __future__ import annotations

import argparse
import getpass
import sys
import time

from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.crypto import generate_key, rotate_token
from app.core.logging import configure_logging
from app.db.session import session_scope


def cmd_gen_secrets(_: argparse.Namespace) -> None:
    import secrets

    print(f"JWT_SECRET={secrets.token_urlsafe(48)}")
    print(f"ENCRYPTION_KEYS={generate_key()}")
    print(f"WEBHOOK_SECRET={secrets.token_urlsafe(32)}")


def cmd_create_user(a: argparse.Namespace) -> None:
    from app.core.security import hash_password, validate_password_strength
    from app.models import User
    from app.services import audit
    from app.services.principal import SYSTEM

    pw = a.password or getpass.getpass("Password: ")
    validate_password_strength(pw)
    with session_scope() as db:
        if db.execute(select(User).where(User.email == a.email.lower())).scalar_one_or_none():
            sys.exit(f"User {a.email} already exists")
        u = User(
            email=a.email.lower(), full_name=a.name or "", role=a.role, hashed_password=hash_password(pw)
        )
        db.add(u)
        db.flush()
        audit.record(
            db,
            SYSTEM,
            "user.created",
            entity_type="user",
            entity_id=u.id,
            details={"role": a.role, "via": "cli"},
        )
    print(f"created {a.role} {a.email}")


def cmd_create_api_key(a: argparse.Namespace) -> None:
    from app.core.security import generate_api_key
    from app.models import ApiKey
    from app.services import audit
    from app.services.principal import SYSTEM

    raw, prefix, h = generate_api_key()
    with session_scope() as db:
        k = ApiKey(name=a.name, prefix=prefix, key_hash=h, role=a.role)
        db.add(k)
        db.flush()
        audit.record(
            db,
            SYSTEM,
            "api_key.created",
            entity_type="api_key",
            entity_id=k.id,
            details={"name": a.name, "via": "cli"},
        )
    print(raw)


def cmd_init_db(_: argparse.Namespace) -> None:
    s = get_settings()
    if s.is_sqlite:
        from app.db.session import get_engine
        from app.models import Base

        Base.metadata.create_all(get_engine())
        print(f"SQLite schema ready: {s.database_url}")
    else:
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        subprocess.run(["alembic", "upgrade", "head"], check=True, cwd=root)  # noqa: S603, S607
        print("migrations applied")


def _index_all(systems: list[str]) -> None:
    from app.knowledge import repository
    from app.rag.embeddings import get_embedder
    from app.rag.retriever import index_knowledge_base
    from app.rag.vector_store import collection_name, get_vector_store

    for system in systems:
        with session_scope() as db:
            snap = repository.get_snapshot(db, system)
            if snap is None:
                print(f"{system}: not loaded, skipping")
                continue
            expected = sum(1 for c in snap.codes.values() if c.billable or c.attributes.get("range"))
            name = collection_name(system, snap.version, get_embedder().name)
            have = get_vector_store().count(name)
        if have >= expected:
            print(f"{system}: vector index up to date ({have} codes)")
            continue
        t = time.time()
        print(f"{system}: embedding {expected} codes with {get_embedder().name}")
        with session_scope() as db:
            n = index_knowledge_base(db, system, progress=_progress)
        print(f"{system}: indexed {n} codes in {time.time() - t:.1f}s")


def _progress(done: int, total: int) -> None:
    print(f"\r  indexed {done}/{total}", end="", flush=True)
    if done == total:
        print()


def cmd_kb(a: argparse.Namespace) -> None:
    from app.knowledge import repository
    from app.knowledge.icd10cm import default_tabular_path, file_checksum, parse_tabular
    from app.knowledge.other_systems import parse_cpt, parse_hcpcs
    from app.models import KnowledgeBaseVersion

    if a.kb_cmd == "load-icd10cm":
        path = a.xml or default_tabular_path()
        if not path:
            sys.exit(
                "No tabular XML found. Download icd10cm-tabular-<YEAR>.xml from CDC and pass --xml, "
                "or `pip install simple-icd-10-cm`."
            )
        t = time.time()
        version, records = parse_tabular(path)
        version = a.version or version
        from app.knowledge.cms_loaders import fiscal_year_window

        # FY N is valid for dates of service Oct 1 (N-1) - Sep 30 (N): lets freshness checks flag an expired set
        window = fiscal_year_window(int(version)) if str(version).isdigit() else (None, None)
        with session_scope() as db:
            kbv = repository.load_records(
                db,
                "ICD-10-CM",
                version,
                records,
                source=f"CDC ICD-10-CM tabular {path}",
                checksum=file_checksum(path),
                effective_from=window[0],
                effective_to=window[1],
            )
            print(
                f"loaded ICD-10-CM {version}: {kbv.code_count} codes "
                f"({sum(r.billable for r in records)} billable) in {time.time() - t:.1f}s"
            )
    elif a.kb_cmd in ("load-hcpcs", "load-cpt"):
        system = "HCPCS" if a.kb_cmd == "load-hcpcs" else "CPT"
        records = (
            parse_hcpcs(a.file)
            if system == "HCPCS"
            else parse_cpt(a.file, license_acknowledged=a.i_have_a_cpt_license)
        )
        src = f"{system} file {a.file}" + (" (licensed; acknowledged via CLI)" if system == "CPT" else "")
        with session_scope() as db:
            kbv = repository.load_records(
                db, system, a.version, records, source=src, checksum=file_checksum(a.file)
            )
            print(
                f"loaded {system} {a.version}: {kbv.code_count} codes. Add {system} to ENABLED_CODE_SYSTEMS to use it."
            )
    elif a.kb_cmd == "index":
        systems = ["ICD-10-CM", "ICD-10-PCS", "HCPCS", "CPT"] if a.system == "all" else [a.system]
        _index_all(systems)
    elif a.kb_cmd == "load-all":
        from app.knowledge.loader_service import load_directory

        with session_scope() as db:
            load_directory(db, a.data_dir, cpt_license=a.i_have_a_cpt_license)
        print("Next: python -m app.cli kb index --system all")
    elif a.kb_cmd == "load-file":
        from app.knowledge.loader_service import load_file

        with session_scope() as db:
            load_file(db, a.file, a.kind, cpt_license=a.i_have_a_cpt_license)
    elif a.kb_cmd == "freshness":
        from datetime import date

        from app.knowledge.loader_service import active_versions, freshness_warnings

        dos = date.fromisoformat(a.dos) if a.dos else date.today()
        with session_scope() as db:
            for v in active_versions(db):
                ok = (not v.effective_to or dos <= v.effective_to) and (
                    not v.effective_from or dos >= v.effective_from
                )
                print(
                    f"{'OK     ' if ok else 'EXPIRED' if v.effective_to and dos > v.effective_to else 'FUTURE '} {v.code_system:12} {v.version:10} {v.effective_from} .. {v.effective_to}"
                )
            warns = freshness_warnings(db, dos)
        if warns:
            print(
                f"\n{len(warns)} reference set(s) not valid for {dos}. Download the current releases (see docs/DATA_SOURCES.md)."
            )
    elif a.kb_cmd == "bootstrap":
        # Idempotent first-boot: load ICD-10-CM if no active version, index if not yet indexed.
        from app.rag.retriever import index_knowledge_base

        with session_scope() as db:
            active = repository.active_version(db, "ICD-10-CM")
        if not active:
            print("no active ICD-10-CM knowledge base; loading")
            cmd_kb(argparse.Namespace(kb_cmd="load-icd10cm", xml=a.xml, version=None))
        # Trust the vector store, not the DB flag: a wiped Qdrant volume must trigger re-indexing.
        from app.rag.embeddings import get_embedder
        from app.rag.vector_store import collection_name, get_vector_store

        with session_scope() as db:
            kbv = db.execute(
                select(KnowledgeBaseVersion).where(
                    KnowledgeBaseVersion.code_system == "ICD-10-CM", KnowledgeBaseVersion.active.is_(True)
                )
            ).scalar_one()
            billable = repository.get_snapshot(db, "ICD-10-CM")
            expected = sum(1 for c in billable.codes.values() if c.billable) if billable else 0
            name = collection_name("ICD-10-CM", kbv.version, get_embedder().name)
            have = get_vector_store().count(name)
            needs_index = have < expected
            print(f"vector index: {have}/{expected} codes in {name}")
        if needs_index and not a.skip_index:
            t = time.time()
            with session_scope() as db:
                n = index_knowledge_base(db, "ICD-10-CM", progress=_progress)
            print(f"indexed {n} codes in {time.time() - t:.1f}s")
        print("knowledge base ready")
    elif a.kb_cmd == "status":
        with session_scope() as db:
            for v in db.execute(
                select(KnowledgeBaseVersion).order_by(
                    KnowledgeBaseVersion.code_system, KnowledgeBaseVersion.loaded_at
                )
            ).scalars():
                print(
                    f"{v.code_system:12} {v.version:10} rows={v.code_count:<7} active={v.active!s:5} "
                    f"indexed={v.indexed!s:5} valid={v.effective_from}..{v.effective_to} loaded={v.loaded_at:%Y-%m-%d %H:%M}"
                )


ENCRYPTED_COLUMNS = {
    "documents": ["text"],
    "clinical_entities": ["text", "normalized"],
    "model_runs": ["raw_response"],
    "coding_suggestions": ["entity_text", "rationale"],
    "coding_evidence": ["quote"],
}


def cmd_rotate_keys(_: argparse.Namespace) -> None:
    """Re-encrypt every PHI column and stored original with the first key in ENCRYPTION_KEYS."""
    from app.core.crypto import encrypt_bytes
    from app.ingestion.storage import get_storage

    with session_scope() as db:
        for table, cols in ENCRYPTED_COLUMNS.items():
            for col in cols:
                rows = db.execute(text(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL")).all()  # noqa: S608
                for rid, val in rows:
                    db.execute(
                        text(f"UPDATE {table} SET {col} = :v WHERE id = :id"),  # noqa: S608
                        {"v": rotate_token(val), "id": rid},
                    )
                print(f"rotated {table}.{col}: {len(rows)} rows")
        storage = get_storage()
        keys = (
            db.execute(text("SELECT storage_key FROM documents WHERE storage_key IS NOT NULL"))
            .scalars()
            .all()
        )
        for key in keys:
            path = storage._path(key)  # noqa: SLF001
            if path.exists():
                path.write_bytes(encrypt_bytes(storage.get(key)))
        print(f"re-encrypted {len(keys)} stored files")
    print("Done. The old key can be removed from ENCRYPTION_KEYS once backups made with it have expired.")


def cmd_claims(a: argparse.Namespace) -> None:
    import json
    from pathlib import Path

    from app.claims.scrubber import Claim, ClaimScrubber

    data = json.loads(Path(a.file).read_text())
    claims = data if isinstance(data, list) else [data]
    with session_scope() as db:
        scrubber = ClaimScrubber(db)
        for c in claims:
            res = scrubber.scrub(Claim.from_dict(c))
            if a.json:
                print(json.dumps(res.as_dict(), default=str, indent=2))
                continue
            print(f"\n== {c.get('claim_id', 'claim')}: {res.summary()}")
            for i in res.issues:
                where = f"line {i.line}" if i.line else "claim"
                carc = f" [CARC {i.carc}{'/' + i.rarc if i.rarc else ''}]" if i.carc else ""
                print(f"  {i.severity.upper():6} {where:8} {i.message}{carc}")
                if i.fix:
                    print(f"         fix: {i.fix}")
            for n in res.not_checked:
                print(f"  not checked: {n}")


def cmd_code_note(a: argparse.Namespace) -> None:
    from pathlib import Path

    from app.demo.service import code_note

    text = Path(a.file).read_text()
    out = code_note(text, encounter_type=a.encounter, patient_sex=a.sex, patient_age=a.age, dos=a.dos)
    print(out["report"])


def cmd_verify_audit(_: argparse.Namespace) -> None:
    from app.services.audit import verify_chain

    with session_scope() as db:
        r = verify_chain(db)
    print(r)
    if not r["valid"]:
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    s = get_settings()
    configure_logging(s.log_level, json_logs=False)
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-secrets").set_defaults(fn=cmd_gen_secrets)
    sub.add_parser("init-db").set_defaults(fn=cmd_init_db)
    cl = sub.add_parser("claims")
    cls_ = cl.add_subparsers(dest="claims_cmd", required=True)
    sc = cls_.add_parser("scrub")
    sc.add_argument("file")
    sc.add_argument("--json", action="store_true")
    cl.set_defaults(fn=cmd_claims)
    cn = sub.add_parser("code-note")
    cn.add_argument("file")
    cn.add_argument("--encounter", default="outpatient", choices=["outpatient", "inpatient"])
    cn.add_argument("--sex", choices=["M", "F"])
    cn.add_argument("--age", type=int)
    cn.add_argument("--dos", help="date of service YYYY-MM-DD (default today)")
    cn.set_defaults(fn=cmd_code_note)
    u = sub.add_parser("create-user")
    u.add_argument("email")
    u.add_argument("--role", default="coder", choices=["admin", "coder", "auditor"])
    u.add_argument("--name")
    u.add_argument("--password")
    u.set_defaults(fn=cmd_create_user)
    k = sub.add_parser("create-api-key")
    k.add_argument("name")
    k.add_argument("--role", default="service", choices=["service", "auditor"])
    k.set_defaults(fn=cmd_create_api_key)
    kb = sub.add_parser("kb")
    kbs = kb.add_subparsers(dest="kb_cmd", required=True)
    li = kbs.add_parser("load-icd10cm")
    li.add_argument("--xml")
    li.add_argument("--version")
    for name in ("load-hcpcs", "load-cpt"):
        lp = kbs.add_parser(name)
        lp.add_argument("file")
        lp.add_argument("--version", required=True)
        if name == "load-cpt":
            lp.add_argument("--i-have-a-cpt-license", action="store_true")
    ix = kbs.add_parser("index")
    ix.add_argument("--system", default="ICD-10-CM", help="ICD-10-CM | ICD-10-PCS | HCPCS | CPT | all")
    kbs.add_parser("status")
    la = kbs.add_parser("load-all")
    la.add_argument("--data-dir", required=True)
    la.add_argument("--i-have-a-cpt-license", action="store_true")
    lf = kbs.add_parser("load-file")
    lf.add_argument("file")
    lf.add_argument(
        "--kind",
        choices=[
            "icd10cm_order",
            "icd10cm_codes",
            "icd10pcs",
            "hcpcs",
            "cpt",
            "mue",
            "proc_notes",
            "ptp",
            "fee",
            "coverage",
            "guidelines",
        ],
    )
    lf.add_argument("--i-have-a-cpt-license", action="store_true")
    fr = kbs.add_parser("freshness")
    fr.add_argument("--dos")
    bs = kbs.add_parser("bootstrap")
    bs.add_argument("--xml")
    bs.add_argument("--skip-index", action="store_true")
    kb.set_defaults(fn=cmd_kb)
    sub.add_parser("rotate-keys").set_defaults(fn=cmd_rotate_keys)
    sub.add_parser("verify-audit").set_defaults(fn=cmd_verify_audit)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
