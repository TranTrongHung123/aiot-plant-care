from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, field_validator

DEVICE_ID = "sim-esp32-01"
PREFIX = "plantcare/v1/simulation"


class Readings(BaseModel):
    temperature_c: float = Field(ge=-20, le=70)
    humidity_pct: float = Field(ge=0, le=100)
    soil_moisture_raw: int = Field(ge=0, le=4095)
    soil_moisture_pct: float | None = Field(default=None, ge=0, le=100)
    pressure_hpa: float = Field(ge=800, le=1200)


class Telemetry(BaseModel):
    schema_version: Literal[1]
    message_id: UUID
    device_id: Literal["sim-esp32-01"]
    source: Literal["simulation"]
    measured_at: AwareDatetime
    boot_id: UUID
    sequence: int = Field(ge=0)
    readings: Readings


class PumpRequest(BaseModel):
    command_id: UUID
    action: Literal["pump_on", "pump_off"]
    duration_seconds: int = Field(default=5, ge=1, le=15)


class PolicyRequest(BaseModel):
    enabled: bool
    threshold: float = Field(ge=10, le=60)
    duration_seconds: int = Field(ge=1, le=15)
    cooldown_seconds: int = Field(ge=30, le=3600)
    schedule_kind: Literal["daily", "interval"] = "daily"
    daily_times: list[str] = Field(default=["06:00", "18:00"], min_length=1, max_length=24)
    interval_minutes: int = Field(default=60, ge=1, le=1440)

    @field_validator("daily_times")
    @classmethod
    def valid_times(cls, values):
        import re

        if any(not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) for value in values):
            raise ValueError("Giờ tưới phải có dạng HH:MM (00:00–23:59)")
        return sorted(set(values))


class ScenarioRequest(BaseModel):
    scenario: Literal["normal", "dry", "rain", "offline"]


class WeatherModeRequest(BaseModel):
    mode: Literal["demo", "live"]
    demo_rain: bool


class Ack(BaseModel):
    schema_version: Literal[1]
    device_id: Literal["sim-esp32-01"]
    source: Literal["simulation"]
    command_id: UUID
    timestamp: AwareDatetime
    status: Literal["accepted", "rejected", "completed", "failed"]
    reason: str = Field(max_length=200)


def fresh(timestamp: datetime | None, at: datetime, seconds: int = 20):
    return timestamp is not None and -5 <= (at - timestamp).total_seconds() <= seconds
