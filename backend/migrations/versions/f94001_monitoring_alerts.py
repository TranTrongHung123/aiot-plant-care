"""Persist monitoring alerts and event checkpoints."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "f94001"
down_revision = "e93001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "device_id",
            sa.String(80),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("trigger_type", sa.String(9), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(80), nullable=True),
        sa.Column("data_source", sa.String(32), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "trigger_type = 'condition' OR (trigger_type = 'event' AND source_ref IS NOT NULL)",
            name="ck_agent_alert_trigger",
        ),
    )
    op.create_index(
        "uq_agent_alert_open_condition",
        "agent_alerts",
        ["device_id", "kind"],
        unique=True,
        postgresql_where=sa.text("trigger_type = 'condition' AND resolved_at IS NULL"),
    )
    op.create_index(
        "uq_agent_alert_event",
        "agent_alerts",
        ["device_id", "kind", "source_ref"],
        unique=True,
        postgresql_where=sa.text("trigger_type = 'event'"),
    )
    op.create_index("ix_agent_alert_history", "agent_alerts", ["device_id", "created_at", "id"])
    op.create_table(
        "monitoring_checkpoints",
        sa.Column(
            "device_id",
            sa.String(80),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("command_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("command_id", sa.String(36), nullable=False),
        sa.Column("job_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
    )


def downgrade():
    op.drop_table("monitoring_checkpoints")
    op.drop_table("agent_alerts")
