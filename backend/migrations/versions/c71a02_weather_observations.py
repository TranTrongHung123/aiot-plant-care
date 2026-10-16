"""Lưu quan trắc OpenWeather; giữ nguyên lịch sử Mock cũ."""

import sqlalchemy as sa
from alembic import op

revision = "c71a02"
down_revision = "b2396064576b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "weather_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("site", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("readings", sa.JSON(), nullable=False),
    )
    op.create_index("ix_weather_observations_site", "weather_observations", ["site"])
    op.create_index("ix_weather_observations_received_at", "weather_observations", ["received_at"])


def downgrade():
    op.drop_table("weather_observations")
