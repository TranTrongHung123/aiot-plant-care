"""Opt-in: runs against the running demo, creates simulation records, never live hardware."""

import io
import json
import os
import time
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from websockets.sync.client import connect

from app.core.settings import settings

pytestmark = pytest.mark.skipif(os.getenv("RUN_E2E") != "1", reason="Start demo and set RUN_E2E=1")


def wait_for(fn, timeout=90):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = fn()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError("Timed out waiting for E2E state")


@pytest.fixture
def api():
    with httpx.Client(
        base_url="http://localhost:8000/api/v1",
        timeout=15,
        headers={"Authorization": f"Bearer {settings.app_token}"},
    ) as client:
        yield client


def test_auth_websocket_and_telemetry(api):
    assert httpx.get("http://localhost:8000/api/v1/dashboard").status_code == 401
    dashboard = wait_for(lambda: d if (d := api.get("/dashboard").json())["latest"] else None)
    assert dashboard["source"] == "simulation"
    assert dashboard["latest"]["source"] == "simulation"
    with connect("ws://localhost:8000/ws", origin="http://localhost:3000") as socket:
        socket.send(json.dumps({"token": settings.app_token}))
        packet = json.loads(socket.recv(timeout=5))
        assert packet["type"] == "snapshot" and packet["data"]["ai_provider"] == "trained"


def test_all_three_ai_jobs_and_private_images(api):
    assert (
        api.post(
            "/images?purpose=leaf", files={"file": ("fake.jpg", b"bad", "image/jpeg")}
        ).status_code
        == 422
    )
    buf = io.BytesIO()
    Image.new("RGB", (300, 220), "#496c38").save(buf, "JPEG")
    jobs = []
    for purpose in ["leaf", "fruit"]:
        response = api.post(
            f"/images?purpose={purpose}",
            files={"file": ("fixture.jpg", buf.getvalue(), "image/jpeg")},
        )
        assert response.status_code == 202, response.text
        jobs.append(response.json())
    response = api.post("/ai/weather")
    # Live endpoint must be unavailable until real observations/configuration exist.
    assert response.status_code in (202, 409), response.text
    if response.status_code == 202:
        jobs.append(response.json())
    from app.api.routes import serialize
    from app.db.session import Session

    with Session.begin() as db:
        replay = replay_job()
        db.add(replay)
        db.flush()
        jobs.append(serialize(replay))
    for job in jobs:
        result = wait_for(
            lambda job=job: (
                j if (j := api.get(f"/jobs/{job['id']}").json())["status"] == "completed" else None
            )
        )
        assert result["result"]["is_mock"] is False
        assert result["provider"] == "trained"
        if job["image_id"]:
            path = f"/images/{job['image_id']}/content"
            assert api.get(path).headers["content-type"] == "image/jpeg"
            assert httpx.get("http://localhost:8000/api/v1" + path).status_code == 401


def test_commands_ack_duplicates_offline_and_auto(api):
    snapshot = api.get("/dashboard").json()
    original = snapshot["policy"]
    original_weather = snapshot["weather_control"]
    policy = {
        "enabled": False,
        "threshold": 30,
        "duration_seconds": 1,
        "cooldown_seconds": 30,
        "schedule_kind": "interval",
        "interval_minutes": 1,
        "daily_times": ["06:00", "18:00"],
    }
    try:
        assert api.put("/irrigation/policy", json=policy).status_code == 200
        rain_mode = api.put("/weather/mode", json={"mode": "demo", "demo_rain": True})
        assert rain_mode.status_code == 200 and rain_mode.json()["rain_expected"] is True
        assert rain_mode.json()["rain_probability"] is None
        assert api.post("/ai/weather").status_code == 409
        api.post("/simulation/scenario", json={"scenario": "normal"}).raise_for_status()
        wait_for(lambda: api.get("/dashboard").json()["device"]["online"])
        request = {"command_id": str(uuid4()), "action": "pump_on", "duration_seconds": 1}

        def attempt():
            response = api.post("/irrigation/commands", json=request)
            assert response.status_code in (202, 409), response.text
            return response.json() if response.status_code == 202 else None

        command = wait_for(attempt, timeout=65)
        assert api.post("/irrigation/commands", json=request).json()["id"] == command["id"]
        assert (
            api.post("/irrigation/commands", json={**request, "duration_seconds": 2}).status_code
            == 409
        )
        wait_for(
            lambda: any(
                c["id"] == command["id"] and c["status"] == "completed"
                for c in api.get("/dashboard").json()["commands"]
            )
        )
        stop = {"command_id": str(uuid4()), "action": "pump_off", "duration_seconds": 1}
        assert api.post("/irrigation/commands", json=stop).status_code == 202
        api.post("/simulation/scenario", json={"scenario": "offline"}).raise_for_status()
        wait_for(lambda: not api.get("/dashboard").json()["device"]["online"])
        assert (
            api.post(
                "/irrigation/commands", json={**request, "command_id": str(uuid4())}
            ).status_code
            == 409
        )
        api.post("/simulation/scenario", json={"scenario": "dry"}).raise_for_status()
        api.put("/irrigation/policy", json={**policy, "enabled": True}).raise_for_status()
        blocked = wait_for(
            lambda: (
                p
                if (p := api.get("/dashboard").json()["policy"])["last_schedule_status"]
                == "skipped"
                else None
            ),
            timeout=85,
        )
        assert "DEMO có mưa" in blocked["last_schedule_reason"]
        assert not any(
            c["origin"] == "automatic" and c["issued_at"] > command["issued_at"]
            for c in api.get("/dashboard").json()["commands"]
        )
        api.put("/weather/mode", json={"mode": "demo", "demo_rain": False}).raise_for_status()
        wait_for(
            lambda: any(
                c["origin"] == "automatic" and c["issued_at"] > command["issued_at"]
                for c in api.get("/dashboard").json()["commands"]
            ),
            timeout=85,
        )
        automatic = next(
            c
            for c in api.get("/dashboard").json()["commands"]
            if c["origin"] == "automatic" and c["issued_at"] > command["issued_at"]
        )
        assert automatic["weather_context"]["source"] == "demo_scenario"
        assert automatic["weather_context"]["rain_expected"] is False
        assert automatic["weather_context"]["schedule_at"]
    finally:
        api.put("/irrigation/policy", json={**policy, "enabled": False})
        api.put("/weather/mode", json=original_weather)
        api.put("/irrigation/policy", json=original)
        api.post("/simulation/scenario", json={"scenario": "normal"})
        api.post(
            "/irrigation/commands",
            json={"command_id": str(uuid4()), "action": "pump_off", "duration_seconds": 1},
        )


def test_worker_recovers_expired_lease_and_reports_storage_failure(api):
    from datetime import timedelta

    from app.core.schemas import DEVICE_ID
    from app.db.models import Job, PlantImage, now
    from app.db.session import Session
    from app.modules.trained_ai import model_metadata

    image_id, missing_job_id, lease_job_id = (str(uuid4()) for _ in range(3))
    with Session.begin() as db:
        db.add(
            PlantImage(
                id=image_id,
                device_id=DEVICE_ID,
                purpose="leaf",
                object_key=f"test-missing/{image_id}.jpg",
                content_type="image/jpeg",
                width=10,
                height=10,
            )
        )
        db.flush()
        db.add(
            Job(
                id=missing_job_id,
                task="leaf",
                image_id=image_id,
                input={},
                model_version=model_metadata("leaf")["model_version"],
            )
        )
        db.add(
            replay_job(
                id=lease_job_id,
                status="running",
                attempts=1,
                lease_until=now() - timedelta(seconds=1),
            )
        )
    try:
        recovered = wait_for(
            lambda: (
                j
                if (j := api.get(f"/jobs/{lease_job_id}").json())["status"] == "completed"
                else None
            )
        )
        assert recovered["attempts"] == 2
        failed = wait_for(
            lambda: (
                j
                if (j := api.get(f"/jobs/{missing_job_id}").json())["status"] == "failed"
                else None
            )
        )
        assert failed["attempts"] == 3 and failed["error"]
    finally:
        with Session.begin() as db:
            for job_id in [missing_job_id, lease_job_id]:
                db.delete(db.get(Job, job_id))
            db.flush()
            db.delete(db.get(PlantImage, image_id))


def replay_job(**kwargs):
    from app.db.models import Job
    from app.modules.trained_ai import model_metadata

    return Job(
        task="weather",
        source="test_fixture",
        provider="trained",
        model_version=model_metadata("weather")["model_version"],
        input={
            "as_of": "2026-01-01T05:00:00+00:00",
            "weather_source": "open_meteo_archive",
            "hourly": [
                {
                    "time": f"2026-01-01T{h:02}:00:00+00:00",
                    "temperature_c": 25,
                    "humidity_pct": 80,
                    "surface_pressure_hpa": 1000,
                    "cloud_cover_pct": 70,
                    "wind_speed_ms": 2,
                }
                for h in range(6)
            ],
        },
        **kwargs,
    )


def test_legacy_mock_job_is_not_relabelled_or_executed(api):
    from app.db.models import Job
    from app.db.session import Session

    job_id = str(uuid4())
    with Session.begin() as db:
        db.add(
            Job(
                id=job_id,
                task="weather",
                provider="mock",
                model_version="mock-v1",
                input={"humidity_pct": 90},
            )
        )
    try:
        job = wait_for(
            lambda: j if (j := api.get(f"/jobs/{job_id}").json())["status"] == "failed" else None
        )
        assert job["provider"] == "mock" and job["model_version"] == "mock-v1"
        assert job["result"] is None and job["attempts"] == 0
        assert api.post(f"/jobs/{job_id}/retry").status_code == 409
    finally:
        with Session.begin() as db:
            db.delete(db.get(Job, job_id))
