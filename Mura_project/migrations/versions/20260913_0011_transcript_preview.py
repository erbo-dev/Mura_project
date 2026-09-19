"""Early transcript preview on recordings.

Revision ID: 20260913_0011
Revises: 20260719_0010
Create Date: 2026-09-13

Purely additive. Recognition finishes within seconds, but the finished result is
written only after extraction, which takes most of a minute. This column holds
the recognised text from the moment it exists so a speaker can read their own
words while people, events and stories are still being found.

It lives on `recordings`, not in `pipeline_results`: that table means "finished",
and replay, completion and the archive all read it that way. A partial row there
would present an unfinished recording as a finished one.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0011"
down_revision: str | None = "20260719_0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("recordings", sa.Column("transcript_preview", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("recordings", "transcript_preview")
