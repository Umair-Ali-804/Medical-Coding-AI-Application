"""claim edits, guideline chunks, code attributes, reference effective dates

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("code_references", sa.Column("attributes", JSON, nullable=True))
    op.add_column("coding_suggestions", sa.Column("guidelines", JSON, nullable=True))
    op.add_column("kb_versions", sa.Column("effective_from", sa.Date(), nullable=True))
    op.add_column("kb_versions", sa.Column("effective_to", sa.Date(), nullable=True))
    op.create_table(
        "claim_edits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("edit_set", sa.String(length=20), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("code2", sa.String(length=20), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("indicator", sa.String(length=10), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claim_edits")),
    )
    op.create_index("ix_claim_edits_lookup", "claim_edits", ["edit_set", "version", "code"], unique=False)
    op.create_table(
        "guideline_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code_system", sa.String(length=20), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("ref", sa.String(length=60), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("code_range", sa.String(length=60), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guideline_chunks")),
    )
    op.create_index(
        "ix_guideline_chunks_system_version", "guideline_chunks", ["code_system", "version"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_guideline_chunks_system_version", table_name="guideline_chunks")
    op.drop_table("guideline_chunks")
    op.drop_index("ix_claim_edits_lookup", table_name="claim_edits")
    op.drop_table("claim_edits")
    op.drop_column("kb_versions", "effective_to")
    op.drop_column("kb_versions", "effective_from")
    op.drop_column("coding_suggestions", "guidelines")
    op.drop_column("code_references", "attributes")
