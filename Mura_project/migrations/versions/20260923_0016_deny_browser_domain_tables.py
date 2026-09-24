"""Deny Supabase browser roles direct access to private MURA tables.

Revision ID: 20260923_0016
Revises: 20260923_0015
Create Date: 2026-09-23

MURA's trust boundary is Browser -> Next.js server -> FastAPI -> PostgreSQL.
Supabase anon/authenticated roles are therefore not data-plane roles for private
MURA domain tables.  The DO block is conditional so ordinary PostgreSQL
deployments/CI, where those Supabase roles do not exist, remain unaffected.

Downgrade intentionally does not recreate browser grants: the migration cannot
know what privileges (if any) a particular deployment had before hardening, and
guessing would risk exposing private family data.
"""

from __future__ import annotations

from alembic import op

revision: str = "20260923_0016"
down_revision: str | None = "20260923_0015"
branch_labels: str | None = None
depends_on: str | None = None

_PRIVATE_TABLES = (
    "ai_usage_events",
    "archive_claims",
    "archive_conflict_decisions",
    "archive_conflicts",
    "archive_corrections",
    "archive_people",
    "book_chapters",
    "book_continuity_states",
    "book_exports",
    "book_jobs",
    "book_plans",
    "book_source_snapshots",
    "books",
    "families",
    "family_graph_edges",
    "family_memberships",
    "materialized_person_profiles",
    "pipeline_replay_runs",
    "pipeline_results",
    "processing_jobs",
    "processing_trace_events",
    "recordings",
    "release_control",
    "release_decisions",
    "storage_cleanup_jobs",
    "users",
    "worker_registrations",
)


def _sql_array(values: tuple[str, ...]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


def upgrade() -> None:
    tables = _sql_array(_PRIVATE_TABLES)
    op.execute(
        f"""
        DO $mura$
        DECLARE
            browser_role text;
            private_table text;
        BEGIN
            FOREACH browser_role IN ARRAY ARRAY['anon', 'authenticated']
            LOOP
                IF EXISTS (
                    SELECT 1 FROM pg_roles WHERE rolname = browser_role
                ) THEN
                    FOREACH private_table IN ARRAY ARRAY[{tables}]
                    LOOP
                        IF to_regclass(format('public.%I', private_table)) IS NOT NULL THEN
                            EXECUTE format(
                                'REVOKE ALL PRIVILEGES ON TABLE public.%I FROM %I',
                                private_table,
                                browser_role
                            );
                        END IF;
                    END LOOP;
                END IF;
            END LOOP;
        END
        $mura$;
        """
    )


def downgrade() -> None:
    # Security-hardening migration: do not guess and recreate historical grants.
    pass
