"""add research_states table (§12.11 chat follow-up)

Revision ID: a3f5d8e2b1c4
Revises: 0a4eb2ca56ea
Create Date: 2026-07-19 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a3f5d8e2b1c4"
down_revision: Union[str, None] = "0a4eb2ca56ea"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_states",
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("papers", sa.Text(), nullable=True, server_default="[]"),
        sa.Column("analyses", sa.Text(), nullable=True, server_default="[]"),
        sa.Column("draft", sa.Text(), nullable=True, server_default=""),
        sa.Column("feedback", sa.Text(), nullable=True, server_default=""),
        sa.Column("download_failures", sa.Text(), nullable=True, server_default="[]"),
        sa.Column("search_queries", sa.Text(), nullable=True, server_default="[]"),
        sa.Column("sub_questions", sa.Text(), nullable=True, server_default="[]"),
        sa.Column("iteration", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )


def downgrade() -> None:
    op.drop_table("research_states")