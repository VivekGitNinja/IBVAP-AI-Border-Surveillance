"""Add operator_suppressions and incident_outbox tables

Revision ID: 0004_add_suppressions_and_outbox
Revises: 0003_add_camera_sector
Create Date: 2026-09-17 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0004_add_suppressions_and_outbox'
down_revision: Union[str, None] = '0003_add_camera_sector'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # 1. Operator Suppressions Table
    if 'operator_suppressions' not in existing_tables:
        op.create_table(
            'operator_suppressions',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('suppression_key', sa.String(length=128), nullable=False),
            sa.Column('entity_type', sa.String(length=20), server_default='track', nullable=False),
            sa.Column('camera_id', sa.Integer(), nullable=True),
            sa.Column('track_id', sa.Integer(), nullable=True),
            sa.Column('dossier_id', sa.String(length=64), nullable=True),
            sa.Column('incident_id', sa.Integer(), nullable=False),
            sa.Column('dismissal_reason', sa.String(length=80), nullable=False),
            sa.Column('operator_notes', sa.Text(), server_default='', nullable=False),
            sa.Column('operator_id', sa.String(length=80), server_default='OPERATOR', nullable=False),
            sa.Column('initial_zone_type', sa.String(length=40), server_default='BUFFER', nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('is_revoked', sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column('revoked_at', sa.DateTime(), nullable=True),
            sa.Column('revocation_reason', sa.String(length=120), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_operator_suppressions_suppression_key', 'operator_suppressions', ['suppression_key'])
        op.create_index('ix_operator_suppressions_dossier_id', 'operator_suppressions', ['dossier_id'])
        op.create_index('ix_operator_suppressions_expires_at', 'operator_suppressions', ['expires_at'])
        op.create_index('ix_operator_suppressions_is_revoked', 'operator_suppressions', ['is_revoked'])
        op.create_index(
            'ix_operator_suppressions_key_active',
            'operator_suppressions',
            ['suppression_key', 'is_revoked', 'expires_at']
        )

    # 2. Incident Outbox Table
    if 'incident_outbox' not in existing_tables:
        op.create_table(
            'incident_outbox',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('event_id', sa.String(length=36), nullable=False),
            sa.Column('event_type', sa.String(length=40), nullable=False),
            sa.Column('incident_code', sa.String(length=40), nullable=False),
            sa.Column('idempotency_key', sa.String(length=36), nullable=False),
            sa.Column('payload', sa.JSON(), nullable=False),
            sa.Column('status', sa.String(length=20), server_default='PENDING', nullable=False),
            sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False),
            sa.Column('lease_until', sa.DateTime(), nullable=True),
            sa.Column('worker_id', sa.String(length=80), nullable=True),
            sa.Column('next_retry_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('delivered_at', sa.DateTime(), nullable=True),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('event_id', name='uq_incident_outbox_event_id'),
            sa.UniqueConstraint('idempotency_key', name='uq_incident_outbox_idempotency_key')
        )
        op.create_index('ix_incident_outbox_incident_code', 'incident_outbox', ['incident_code'])
        op.create_index('ix_incident_outbox_status', 'incident_outbox', ['status'])
        op.create_index('ix_incident_outbox_lease_until', 'incident_outbox', ['lease_until'])
        op.create_index('ix_incident_outbox_next_retry_at', 'incident_outbox', ['next_retry_at'])
        op.create_index(
            'ix_incident_outbox_claim',
            'incident_outbox',
            ['status', 'next_retry_at', 'lease_until']
        )
        op.create_index('ix_incident_outbox_created', 'incident_outbox', ['created_at'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'incident_outbox' in existing_tables:
        op.drop_table('incident_outbox')
    if 'operator_suppressions' in existing_tables:
        op.drop_table('operator_suppressions')
