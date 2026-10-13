"""Canonical RAG chunks, publication pointers, FTS5 and processing metadata."""
from alembic import op
import sqlalchemy as sa

revision = "c8f3a2d7e901"
down_revision = "b7e2f1a9c4d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("processing_stage", sa.String(32), nullable=False, server_default="pending"))
    op.add_column("documents", sa.Column("warnings", sa.Text(), nullable=False, server_default="[]"))
    op.create_table("rag_generations",
        sa.Column("doc_id", sa.String(64), primary_key=True),
        sa.Column("kb_id", sa.String(64), nullable=False),
        sa.Column("generation", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False))
    op.create_index("ix_rag_generations_kb_id", "rag_generations", ["kb_id"])
    op.create_table("rag_chunks",
        sa.Column("chunk_id", sa.String(160), primary_key=True),
        sa.Column("doc_id", sa.String(64), nullable=False),
        sa.Column("kb_id", sa.String(64), nullable=False),
        sa.Column("generation", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("terms", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False))
    for column in ("doc_id", "kb_id", "generation"):
        op.create_index(f"ix_rag_chunks_{column}", "rag_chunks", [column])
    op.execute("CREATE VIRTUAL TABLE rag_fts USING fts5(chunk_id UNINDEXED, terms)")
    op.execute("CREATE TRIGGER rag_chunks_ai AFTER INSERT ON rag_chunks BEGIN "
        "INSERT INTO rag_fts(chunk_id, terms) VALUES (new.chunk_id, new.terms); END")
    op.execute("CREATE TRIGGER rag_chunks_ad AFTER DELETE ON rag_chunks BEGIN "
        "DELETE FROM rag_fts WHERE chunk_id = old.chunk_id; END")
    op.execute("CREATE TRIGGER rag_chunks_au AFTER UPDATE ON rag_chunks BEGIN "
        "DELETE FROM rag_fts WHERE chunk_id = old.chunk_id; "
        "INSERT INTO rag_fts(chunk_id, terms) VALUES (new.chunk_id, new.terms); END")


def downgrade() -> None:
    for name in ("rag_chunks_ai", "rag_chunks_ad", "rag_chunks_au"):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.execute("DROP TABLE IF EXISTS rag_fts")
    op.drop_table("rag_chunks")
    op.drop_table("rag_generations")
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("warnings")
        batch.drop_column("processing_stage")
