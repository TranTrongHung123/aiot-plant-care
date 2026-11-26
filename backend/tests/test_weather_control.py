from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.schemas import PolicyRequest, WeatherModeRequest
from app.core.settings import settings
from app.modules.schedule import due_slot, latest_slot
from app.modules.weather_control import active_weather, live_result


def policy(**values):
    return SimpleNamespace(
        enabled=True,
        schedule_kind=values.pop("schedule_kind", "daily"),
        daily_times=["06:00", "18:00"],
        interval_minutes=1,
        schedule_started_at=datetime(2026, 10, 15, 0, tzinfo=UTC),
        last_schedule_at=values.pop("last_schedule_at", None),
        last_schedule_status=values.pop("last_schedule_status", ""),
        **values,
    )


def test_daily_schedule_is_vietnam_time_no_replay_or_catchup():
    # 11:00 UTC is 18:00 Asia/Ho_Chi_Minh.
    at = datetime(2026, 10, 15, 11, tzinfo=UTC)
    item = policy()
    assert due_slot(item, at) == at
    item.last_schedule_at = at
    item.last_schedule_status = "requested"
    assert due_slot(item, at + timedelta(seconds=2)) is None
    assert due_slot(policy(), at + timedelta(minutes=6)) is None
    item.schedule_started_at = at + timedelta(seconds=10)
    assert due_slot(item, at + timedelta(seconds=20)) is None


def test_interval_schedule_waits_full_period_and_rechecks_pending_weather():
    item = policy(schedule_kind="interval")
    start = item.schedule_started_at
    assert due_slot(item, start + timedelta(seconds=59)) is None
    slot = start + timedelta(minutes=1)
    assert due_slot(item, slot) == slot
    item.last_schedule_at, item.last_schedule_status = slot, "waiting_weather"
    assert due_slot(item, slot + timedelta(seconds=30)) == slot
    item.last_schedule_status = "skipped"
    assert due_slot(item, slot + timedelta(seconds=30)) is None
    assert due_slot(item, start + timedelta(minutes=20)) == start + timedelta(minutes=20)
    item.enabled = False
    assert latest_slot(item, start + timedelta(minutes=21)) is None


def test_schedule_and_mode_validation():
    base = dict(enabled=True, threshold=30, duration_seconds=5, cooldown_seconds=30)
    assert PolicyRequest(**base, daily_times=["18:00", "06:00", "06:00"]).daily_times == [
        "06:00",
        "18:00",
    ]
    for changes in (
        {"daily_times": ["24:00"]},
        {"daily_times": []},
        {"interval_minutes": 0},
        {"schedule_kind": "unknown"},
    ):
        with pytest.raises(ValidationError):
            PolicyRequest(**base, **changes)
    with pytest.raises(ValidationError):
        WeatherModeRequest(mode="fake", demo_rain=False)


@pytest.mark.parametrize("rain", [True, False])
def test_demo_is_explicit_and_does_not_need_weather_api_or_model(rain, monkeypatch):
    from app.modules import weather_control

    monkeypatch.setattr(
        weather_control, "model_metadata", lambda _: pytest.fail("DEMO loaded model")
    )
    monkeypatch.setattr(settings, "device_mode", "simulation")
    control = SimpleNamespace(mode="demo", demo_rain=rain, updated_at=datetime.now(UTC))
    db = SimpleNamespace(get=lambda *_: control)
    state = active_weather(db)
    assert state["available"] and state["rain_expected"] == rain
    assert state["source"] == "demo_scenario" and state["rain_probability"] is None
    assert state["model_version"] is None
    monkeypatch.setattr(settings, "device_mode", "live")
    assert not active_weather(db)["available"]


def test_live_rejects_expired_mock_replay_wrong_site_and_version():
    at = datetime(2026, 10, 15, 11, 10, tzinfo=UTC)
    result = {
        "forecast_start": "2026-10-15T11:00:00+00:00",
        "forecast_end": "2026-10-15T12:00:00+00:00",
        "rain_probability": 0.2,
        "threshold": 0.37,
        "prediction": 0,
        "model_version": "v1",
        "source": "openweather_current",
        "is_mock": False,
        "historical_replay": False,
    }
    job = SimpleNamespace(
        provider="trained",
        source="openweather_current",
        status="completed",
        model_version="v1",
        input={"site": "site1"},
        result=result,
    )
    assert live_result(job, at, site="site1", version="v1") == result
    for changes in (
        {"is_mock": True},
        {"historical_replay": True},
        {"forecast_end": "2026-10-15T11:05:00+00:00"},
        {"forecast_start": "2026-10-15T12:00:00+00:00"},
        {"prediction": 1},
        {"rain_probability": float("nan")},
        {"model_version": "v0"},
        {"forecast_start": "bad"},
    ):
        job.result = {**result, **changes}
        assert live_result(job, at, site="site1", version="v1") is None
    job.result = result
    assert live_result(job, at, site="site2", version="v1") is None
    job.source = "test_fixture"
    assert live_result(job, at, site="site1", version="v1") is None


@pytest.mark.parametrize("available,rain", [(True, True), (False, None)])
def test_automatic_command_rechecks_weather_under_lock(monkeypatch, available, rain):
    from contextlib import nullcontext
    from uuid import uuid4

    from fastapi import HTTPException

    from app.core.schemas import PumpRequest
    from app.modules import control

    at = datetime(2026, 10, 15, 11, tzinfo=UTC)
    scheduled = policy(last_schedule_at=at)
    scheduled.threshold, scheduled.duration_seconds = 30, 1
    device = SimpleNamespace(online=True, last_seen=at, pump="off")
    reading = SimpleNamespace(measured_at=at, readings={"soil_moisture_pct": 20})
    rows = iter([device, reading, None, scheduled])
    db = SimpleNamespace(get=lambda *_: None, scalar=lambda _: next(rows))
    monkeypatch.setattr(control, "Session", SimpleNamespace(begin=lambda: nullcontext(db)))
    monkeypatch.setattr(control, "now", lambda: at)
    monkeypatch.setattr(
        control,
        "active_weather",
        lambda *_: {
            "available": available,
            "rain_expected": rain,
            "reason": "weather blocked",
        },
    )
    bridge = SimpleNamespace(connected=True, publish=lambda *_: pytest.fail("Unsafe publish"))
    with pytest.raises(HTTPException, match="weather blocked"):
        control.create_command(
            PumpRequest(command_id=uuid4(), action="pump_on", duration_seconds=1),
            bridge,
            "automatic",
            schedule_at=at,
        )


def test_rain_deferral_triggers_catchup_after_30s_when_soil_stays_dry(monkeypatch):
    from contextlib import nullcontext

    from app.modules import control

    at = datetime(2026, 10, 15, 11, tzinfo=UTC)
    scheduled = policy(threshold=30, duration_seconds=2, cooldown_seconds=30, decision="")
    scheduled.last_schedule_reason = ""
    device = SimpleNamespace(online=True, last_seen=at, pump="off")
    reading = SimpleNamespace(measured_at=at, readings={"soil_moisture_pct": 18})
    published = []
    bridge = SimpleNamespace(connected=True, publish=lambda topic, payload: published.append((topic, payload)))

    class FakeDB:
        def scalar(self, stmt):
            s = str(stmt)
            if "irrigation_policies" in s:
                return scheduled
            if "sensor_readings" in s:
                return reading
            if "devices" in s:
                return device
            return None

        def scalars(self, stmt):
            return []

        def get(self, model, pk):
            return scheduled if model.__name__ == "Policy" else None

        def add(self, obj):
            pass

    monkeypatch.setattr(control, "Session", SimpleNamespace(begin=lambda: nullcontext(FakeDB())))
    monkeypatch.setattr(
        control,
        "active_weather",
        lambda *_: {
            "available": True,
            "rain_expected": True,
            "source": "demo_scenario",
            "reason": "DEMO có mưa: chặn tưới tự động",
        },
    )

    # 1. Tại đúng mốc lịch: hoãn tưới chờ mưa, chưa phát lệnh MQTT.
    monkeypatch.setattr(control, "now", lambda: at)
    control.maintain(bridge)
    assert scheduled.last_schedule_status == "skipped"
    assert "Hoãn tưới chờ mưa" in scheduled.last_schedule_reason
    assert not published

    # 2. Sau 30 giây chờ mưa mà cảm biến đất vẫn khô (18% < 30%): tự động kích hoạt tưới bù.
    later = at + timedelta(seconds=31)
    device.last_seen = reading.measured_at = later
    monkeypatch.setattr(control, "now", lambda: later)
    control.maintain(bridge)
    assert scheduled.last_schedule_status == "requested"
    assert "tưới bù" in scheduled.last_schedule_reason.lower()
    assert len(published) == 1
    assert published[0][1]["action"] == "pump_on"
    assert published[0][1]["duration_ms"] == 2000
