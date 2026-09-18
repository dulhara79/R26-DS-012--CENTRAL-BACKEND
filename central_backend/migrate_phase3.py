"""Versioned Phase 3 schema migration for ForecastResult persistence.

Usage:
    python migrate_phase3.py

Adds only the Phase-3 forecast_results table and an idempotent migration marker.
It does not alter FusionResult or any teammate-owned scientific model state.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, MetaData, String, Table, inspect, select

from db_models import ForecastResult, engine, utcnow

MIGRATION_ID = "phase3_forecast_result_v1"

metadata = MetaData()
schema_migrations = Table(
    "schema_migrations",
    metadata,
    Column("migration_id", String(128), primary_key=True),
    Column("applied_at", DateTime(timezone=True), nullable=False),
)


def apply() -> bool:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "subjects" not in tables or "fusion_results" not in tables or "modality_readings" not in tables:
        raise RuntimeError(
            "Required baseline tables are missing. Apply Phase 3 only to an "
            "initialized Central Backend database."
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

    ForecastResult.__table__.create(engine, checkfirst=True)

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
