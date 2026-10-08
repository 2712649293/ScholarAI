"""drop research_states table (M4.5.1: langgraph checkpointer 替代)

工作流状态（papers/analyses/draft 等）从 DB 表迁到 langgraph checkpointer（thread_id=session_id），
research_states 表不再需要。

Revision ID: b7e2f1a9c4d3
Revises: a3f5d8e2b1c4
Create Date: 2026-07-20 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b7e2f1a9c4d3"
down_revision: Union[str, None] = "a3f5d8e2b1c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("research_states")


def downgrade() -> None:
    # 恢复原 schema（万一要回滚）
    import sqlalchemy as sa

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
