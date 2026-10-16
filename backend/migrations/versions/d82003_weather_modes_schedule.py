"""Chế độ thời tiết và lịch tưới lặp có kiểm soát."""

import sqlalchemy as sa
from alembic import op

revision = "d82003"
down_revision = "c71a02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "weather_control",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("demo_rain", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("INSERT INTO weather_control VALUES (1, 'live', true, CURRENT_TIMESTAMP)")
    op.add_column(
        "irrigation_commands",
        sa.Column("weather_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    for column in (
        sa.Column("schedule_kind", sa.String(), nullable=False, server_default="daily"),
        sa.Column(
            "daily_times",
            sa.JSON(),
            nullable=False,
            server_default=sa.text('\'["06:00","18:00"]\''),
        ),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column(
            "schedule_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_schedule_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_schedule_reason", sa.String(), nullable=False, server_default=""),
        sa.Column("last_schedule_status", sa.String(), nullable=False, server_default=""),
    ):
        op.add_column("irrigation_policies", column)
    op.alter_column("irrigation_commands", "weather_context", server_default=None)
    for name in (
        "schedule_kind",
        "daily_times",
        "interval_minutes",
        "schedule_started_at",
        "last_schedule_reason",
        "last_schedule_status",
    ):
        op.alter_column("irrigation_policies", name, server_default=None)


def downgrade():
    for name in (
        "last_schedule_status",
        "last_schedule_reason",
        "last_schedule_at",
        "schedule_started_at",
        "interval_minutes",
        "daily_times",
        "schedule_kind",
    ):
        op.drop_column("irrigation_policies", name)
    op.drop_column("irrigation_commands", "weather_context")
    op.drop_table("weather_control")
