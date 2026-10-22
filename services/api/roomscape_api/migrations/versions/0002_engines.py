"""2.0 engines: requested/used engine, durable provider state, fallback record

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('reconstruction_jobs', sa.Column('engine', sa.String(length=40), nullable=True))
    op.add_column('reconstruction_jobs', sa.Column('engine_used', sa.String(length=40), nullable=True))
    op.add_column('reconstruction_jobs', sa.Column('provider_state', sa.JSON(), nullable=True))
    op.add_column('reconstruction_jobs', sa.Column('fallback', sa.JSON(), nullable=True))
    op.add_column('reconstruction_jobs', sa.Column('cost_estimate', sa.JSON(), nullable=True))


def downgrade() -> None:
    for c in ('cost_estimate', 'fallback', 'provider_state', 'engine_used', 'engine'):
        op.drop_column('reconstruction_jobs', c)
