from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
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
