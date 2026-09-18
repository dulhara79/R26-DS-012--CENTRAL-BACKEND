"""Versioned Phase 4 schema migration for server-owned attention events.

Usage:
    python migrate_phase4.py

Adds only Phase-4 attention event and durable episode-state tables plus an
idempotent migration marker. Existing forecast/fusion scientific rows are not
rewritten.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, MetaData, String, Table, inspect, select

from db_models import AttentionEpisodeState, AttentionEventRecord, engine, utcnow

MIGRATION_ID = "phase4_attention_engine_v1"

metadata = MetaData()
schema_migrations = Table(
    "schema_migrations",
    metadata,
    Column("migration_id", String(128), primary_key=True),
    Column("applied_at", DateTime(timezone=True), nullable=False),
)


def apply() -> bool:
    tables = set(inspect(engine).get_table_names())
    required = {"subjects", "fusion_results", "forecast_results"}
    if not required.issubset(tables):
        raise RuntimeError(
            "Required baseline tables are missing. Apply Phase 4 only after "
            "the initialized Phase-3 Central Backend schema."
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

    AttentionEventRecord.__table__.create(engine, checkfirst=True)
    AttentionEpisodeState.__table__.create(engine, checkfirst=True)

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
