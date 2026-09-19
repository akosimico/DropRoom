"""add total_size_bytes to files for upload sessions

Revision ID: a1b2c3d4e5f6
Revises: ec16fa0f0980
Create Date: 2026-09-18 19:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "ec16fa0f0980"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("files", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("total_size_bytes", sa.BigInteger(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("files", schema=None) as batch_op:
        batch_op.drop_column("total_size_bytes")