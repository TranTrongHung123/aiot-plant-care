from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(UTC)


def uid():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(default="Cà chua • chậu 01")
    source: Mapped[str] = mapped_column(default="simulation")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    pump: Mapped[str] = mapped_column(default="unknown")
    scenario: Mapped[str] = mapped_column(default="normal")


class Reading(Base):
    __tablename__ = "sensor_readings"
    __table_args__ = (UniqueConstraint("device_id", "message_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    message_id: Mapped[str] = mapped_column(String(36))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    source: Mapped[str] = mapped_column(default="simulation")
    readings: Mapped[dict] = mapped_column(JSON)


class Command(Base):
    __tablename__ = "irrigation_commands"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    action: Mapped[str]
    duration_ms: Mapped[int]
    source: Mapped[str] = mapped_column(default="simulation")
    origin: Mapped[str] = mapped_column(default="manual")
    status: Mapped[str] = mapped_column(default="requested")
    reason: Mapped[str] = mapped_column(default="")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    events: Mapped[list] = mapped_column(JSON, default=list)
    weather_context: Mapped[dict] = mapped_column(JSON, default=dict)


class Policy(Base):
    __tablename__ = "irrigation_policies"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(default=False)
    threshold: Mapped[float] = mapped_column(Float, default=30)
    duration_seconds: Mapped[int] = mapped_column(default=5)
    cooldown_seconds: Mapped[int] = mapped_column(default=60)
    decision: Mapped[str] = mapped_column(default="Tự động đang tắt")
    schedule_kind: Mapped[str] = mapped_column(default="daily")
    daily_times: Mapped[list] = mapped_column(JSON, default=lambda: ["06:00", "18:00"])
    interval_minutes: Mapped[int] = mapped_column(default=60)
    schedule_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_schedule_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_schedule_reason: Mapped[str] = mapped_column(default="")
    last_schedule_status: Mapped[str] = mapped_column(default="")


class PlantImage(Base):
    __tablename__ = "images"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    source: Mapped[str] = mapped_column(default="simulation")
    purpose: Mapped[str]
    object_key: Mapped[str]
    content_type: Mapped[str]
    width: Mapped[int]
    height: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "inference_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    image_id: Mapped[str | None] = mapped_column(ForeignKey("images.id"))
    task: Mapped[str]
    source: Mapped[str] = mapped_column(default="simulation")
    provider: Mapped[str] = mapped_column(default="trained")
    model_version: Mapped[str] = mapped_column(default="unavailable")
    status: Mapped[str] = mapped_column(default="queued", index=True)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None]
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WeatherObservation(Base):
    __tablename__ = "weather_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    site: Mapped[str] = mapped_column(index=True)
    source: Mapped[str] = mapped_column(default="openweather_current")
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    readings: Mapped[dict] = mapped_column(JSON)


class WeatherControl(Base):
    __tablename__ = "weather_control"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(default="live")
    demo_rain: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


chat_json = JSON().with_variant(JSONB(), "postgresql")


class ChatConversation(Base):
    __tablename__ = "chat_conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(100), default="Hội thoại mới")
    default_mode: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    turn_count: Mapped[int] = mapped_column(default=0)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "turn", "role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE")
    )
    turn: Mapped[int]
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24))
    details: Mapped[dict] = mapped_column("metadata", chat_json, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatToolCall(Base):
    __tablename__ = "chat_tool_calls"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True
    )
    agent: Mapped[str] = mapped_column(String(24))
    tool: Mapped[str] = mapped_column(String(16))
    sanitized_args: Mapped[dict] = mapped_column(chat_json)
    sanitized_result: Mapped[dict] = mapped_column(chat_json)
    duration_ms: Mapped[int]
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AgentAlert(Base):
    __tablename__ = "agent_alerts"
    __table_args__ = (
        CheckConstraint(
            "trigger_type = 'condition' OR (trigger_type = 'event' AND source_ref IS NOT NULL)",
            name="ck_agent_alert_trigger",
        ),
        Index(
            "uq_agent_alert_open_condition",
            "device_id",
            "kind",
            unique=True,
            postgresql_where=text("trigger_type = 'condition' AND resolved_at IS NULL"),
            sqlite_where=text("trigger_type = 'condition' AND resolved_at IS NULL"),
        ),
        Index(
            "uq_agent_alert_event",
            "device_id",
            "kind",
            "source_ref",
            unique=True,
            postgresql_where=text("trigger_type = 'event'"),
            sqlite_where=text("trigger_type = 'event'"),
        ),
        Index("ix_agent_alert_history", "device_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    trigger_type: Mapped[str] = mapped_column(String(9))
    severity: Mapped[str] = mapped_column(String(8))
    source_kind: Mapped[str] = mapped_column(String(16))
    source_ref: Mapped[str | None] = mapped_column(String(80))
    data_source: Mapped[str] = mapped_column(String(32))
    evidence: Mapped[dict] = mapped_column(chat_json)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MonitoringCheckpoint(Base):
    __tablename__ = "monitoring_checkpoints"
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    command_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    command_id: Mapped[str] = mapped_column(String(36))
    job_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    job_id: Mapped[str] = mapped_column(String(36))
