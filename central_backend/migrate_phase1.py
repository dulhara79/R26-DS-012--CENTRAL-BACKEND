"""Versioned Phase 1 schema migration for existing baseline databases.

Usage:
    python migrate_phase1.py

The pre-Phase-1 baseline already has subjects and the other CURRENT tables.
This migration adds only the new Phase-1 authorization tables and records an
idempotent migration marker. It does not alter scientific result tables.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, MetaData, String, Table, inspect, select

from db_models import (
    Clinician,
    ClinicianSubjectAssignment,
    engine,
    utcnow,
)

MIGRATION_ID = "phase1_auth_assignments_v1"

metadata = MetaData()
schema_migrations = Table(
    "schema_migrations",
    metadata,
    Column("migration_id", String(128), primary_key=True),
    Column("applied_at", DateTime(timezone=True), nullable=False),
)


def apply() -> bool:
    inspector = inspect(engine)
    if "subjects" not in inspector.get_table_names():
        raise RuntimeError(
            "Baseline subjects table is missing. Apply this migration only to "
            "an initialized Central Backend database."
        )

    schema_migrations.create(engine, checkfirst=True)

    with engine.begin() as conn:
        already = conn.execute(
            select(schema_migrations.c.migration_id).where(
                schema_migrations.c.migration_id == MIGRATION_ID
            )
        ).scalar_one_or_none()
        if already:
            return False

    Clinician.__table__.create(engine, checkfirst=True)
    ClinicianSubjectAssignment.__table__.create(engine, checkfirst=True)

    with engine.begin() as conn:
        conn.execute(
            schema_migrations.insert().values(
                migration_id=MIGRATION_ID,
                applied_at=utcnow(),
            )
        )
    return True


if __name__ == "__main__":
    changed = apply()
    print(f"{MIGRATION_ID}: {'applied' if changed else 'already applied'}")
