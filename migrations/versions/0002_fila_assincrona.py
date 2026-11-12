"""fila do modo assíncrono

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tarefas_extracao",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("id_externo", sa.String(length=200), nullable=True),
        sa.Column("tipo_documento", sa.String(length=40), nullable=False),
        sa.Column("sha256_imagem", sa.String(length=64), nullable=False),
        sa.Column("formato", sa.String(length=10), nullable=False),
        sa.Column("chave_imagem", sa.String(length=200), nullable=False),
        sa.Column("situacao", sa.String(length=15), nullable=False),
        sa.Column("tentativas", sa.Integer(), nullable=False),
        sa.Column("disponivel_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prazo_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trabalhador", sa.String(length=100), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("corpo_erro", sa.Text(), nullable=True),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tarefas_extracao_expira_em", "tarefas_extracao", ["expira_em"], unique=False
    )
    op.create_index(
        "ix_tarefas_fila",
        "tarefas_extracao",
        ["situacao", "disponivel_em", "criado_em"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tarefas_fila", table_name="tarefas_extracao")
    op.drop_index("ix_tarefas_extracao_expira_em", table_name="tarefas_extracao")
    op.drop_table("tarefas_extracao")
