"""Opt-in monitoring storage and API tests in a disposable PostgreSQL schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import alerts as alert_api
from app.core.schemas import DEVICE_ID
from app.core.settings import settings
from app.db.models import AgentAlert, Base, Command, Device, Job, MonitoringCheckpoint, PlantImage
from app.db.session import engine
from app.integrations.chat_llm import Plan
from app.modules import monitoring
from app.modules.chat import execute_plan, read_snapshot

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_MONITORING_POSTGRES") != "1", reason="Requires PostgreSQL"
)
AT = datetime(2026, 11, 22, 10, 20, tzinfo=UTC)


@pytest.fixture
def sessions():
    schema = "monitoring_test_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    isolated = engine.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(isolated)
        factory = sessionmaker(isolated, expire_on_commit=False)
        with factory.begin() as db:
            db.add(Device(id=DEVICE_ID))
        yield factory
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))


def alert(kind="device_offline", trigger="condition", source_ref=DEVICE_ID, **overrides):
    values = {
        "device_id": DEVICE_ID,
        "kind": kind,
        "trigger_type": trigger,
        "severity": "warning",
        "source_kind": "device",
        "source_ref": source_ref,
        "data_source": "simulation",
        "evidence": {"reason": "status_expired"},
        "observed_at": AT,
        "created_at": AT,
    }
    return AgentAlert(**(values | overrides))


def test_postgres_condition_and_event_uniqueness(sessions):
    with sessions.begin() as db:
        first = alert()
        db.add(first)
    with (
        pytest.raises(IntegrityError, match="uq_agent_alert_open_condition"),
        sessions.begin() as db,
    ):
        db.add(alert())
    with sessions.begin() as db:
        db.get(AgentAlert, first.id).resolved_at = AT
    with sessions.begin() as db:
        db.add(alert())

    command_alert = alert("irrigation_error", "event", "command-1", resolved_at=AT)
    with sessions.begin() as db:
        db.add(command_alert)
    with pytest.raises(IntegrityError, match="uq_agent_alert_event"), sessions.begin() as db:
        db.add(alert("irrigation_error", "event", "command-1"))


def add_command(sessions, identifier, when, status="timed_out"):
    with sessions.begin() as db:
        db.add(
            Command(
                id=identifier,
                device_id=DEVICE_ID,
                action="pump_on",
                duration_ms=1000,
                status=status,
                issued_at=when,
                expires_at=when,
                updated_at=when,
                events=[{"status": status, "at": when.isoformat()}],
            )
        )


def add_leaf(sessions, identifier, when):
    with sessions.begin() as db:
        image = PlantImage(
            id=identifier,
            device_id=DEVICE_ID,
            purpose="leaf",
            object_key=identifier,
            content_type="image/jpeg",
            width=100,
            height=100,
            created_at=when,
        )
        db.add(image)
        db.flush()
        db.add(
            Job(
                image_id=image.id,
                task="leaf",
                provider="trained",
                status="completed",
                model_version="v1",
                finished_at=when,
                result={
                    "is_mock": False,
                    "class_name": "Tomato___Early_blight",
                    "confidence": 0.85,
                },
            )
        )


def test_checkpoint_rollback_overlap_and_replay(sessions, monkeypatch):
    add_command(sessions, "historical", AT - timedelta(seconds=5))
    monitoring.evaluate_monitoring(True, AT, sessions)
    with sessions() as db:
        assert db.scalar(select(AgentAlert)) is None
        assert db.get(MonitoringCheckpoint, DEVICE_ID).started_at == AT

    evidence_at = AT + timedelta(seconds=5)
    deadline = AT + timedelta(seconds=15)
    monitoring.evaluate_monitoring(True, deadline, sessions)
    # Simulate a late commit carrying a timestamp older than the saved cursor.
    add_command(sessions, "late-command", evidence_at)
    add_leaf(sessions, "leaf-first", evidence_at)
    add_leaf(sessions, "leaf-repeat", evidence_at + timedelta(seconds=3))

    def fail(*_):
        raise RuntimeError("forced transaction failure")

    retry_at = deadline + timedelta(seconds=1)
    with monkeypatch.context() as patch:
        patch.setattr(monitoring, "_sync_leaf", fail)
        with pytest.raises(RuntimeError, match="forced transaction failure"):
            monitoring.evaluate_monitoring(True, retry_at, sessions)
    with sessions() as db:
        assert db.scalar(select(AgentAlert)) is None
        assert db.get(MonitoringCheckpoint, DEVICE_ID).command_at == deadline

    monitoring.evaluate_monitoring(True, retry_at, sessions)
    recovered_at = retry_at + timedelta(seconds=2)
    with sessions.begin() as db:
        cmd = db.get(Command, "late-command")
        cmd.status, cmd.updated_at = "completed", recovered_at
        cmd.events = [*cmd.events, {"status": "completed", "at": recovered_at.isoformat()}]
    monitoring.evaluate_monitoring(True, recovered_at, sessions)
    monitoring.evaluate_monitoring(True, recovered_at + timedelta(seconds=1), sessions)
    with sessions() as db:
        rows = {row.source_ref: row for row in db.scalars(select(AgentAlert))}
        assert set(rows) == {"late-command", "leaf-first"}
        assert rows["late-command"].observed_at == evidence_at
        assert rows["late-command"].resolved_at == recovered_at


def test_concurrent_ticks_deduplicate_and_cooldown_persists_across_restart(sessions):
    with sessions.begin() as db:
        device = db.get(Device, DEVICE_ID)
        device.last_seen, device.online = AT - timedelta(seconds=46), True
    barrier = Barrier(2)

    def evaluate():
        barrier.wait(timeout=5)
        monitoring.evaluate_monitoring(True, AT, sessions)

    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = [pool.submit(evaluate) for _ in range(2)]
        for run in runs:
            run.result(timeout=8)
    recovery = AT + timedelta(seconds=4)
    with sessions.begin() as db:
        db.get(Device, DEVICE_ID).last_seen = recovery
    monitoring.evaluate_monitoring(True, recovery, sessions)
    monitoring.evaluate_monitoring(True, recovery + timedelta(seconds=55), sessions)
    with sessions() as db:
        assert len(list(db.scalars(select(AgentAlert)))) == 1
    monitoring.evaluate_monitoring(True, recovery + timedelta(seconds=60), sessions)
    with sessions() as db:
        rows = list(db.scalars(select(AgentAlert).order_by(AgentAlert.created_at)))
        assert len(rows) == 2 and rows[0].resolved_at == recovery and rows[1].resolved_at is None


def test_alert_api_filters_cursor_and_acknowledges_once(sessions, monkeypatch):
    monkeypatch.setattr(alert_api, "Session", sessions)
    monkeypatch.setattr(settings, "app_token", "monitoring-test-app-token")
    headers = {"Authorization": "Bearer monitoring-test-app-token"}
    active = alert()
    recovered = alert("sensor_stale", resolved_at=AT)
    event = alert(
        "irrigation_error",
        "event",
        "command-1",
        source_kind="command",
        evidence={"status": "timed_out", "internal_path": "/private/store"},
    )
    with sessions.begin() as db:
        db.add_all([active, recovered, event])
    app = FastAPI()
    app.include_router(alert_api.router)
    client = TestClient(app)
    path = "/api/v1/alerts"
    ack = f"{path}/{active.id}/acknowledge"
    assert client.get(path).status_code == 401
    assert client.get(path, params={"token": "monitoring-test-app-token"}).status_code == 401
    assert client.post(ack).status_code == 401
    attention = client.get(path, headers=headers).json()
    assert {row["id"] for row in attention["items"]} == {active.id, event.id}
    assert attention["unread_count"] == 3
    assert "internal_path" not in next(
        row["evidence"] for row in attention["items"] if row["id"] == event.id
    )
    ids = []
    query = {"state": "all", "limit": 1}
    for _ in range(3):
        res = client.get(path, params=query, headers=headers)
        assert res.status_code == 200
        page = res.json()
        ids.append(page["items"][0]["id"])
        query["cursor"] = page["next_cursor"]
    assert ids == sorted([active.id, recovered.id, event.id], reverse=True)
    assert query["cursor"] is None
    assert client.get(path, params={"cursor": "broken"}, headers=headers).status_code == 422
    assert client.post(f"{path}/{uuid4()}/acknowledge", headers=headers).status_code == 404

    monkeypatch.setattr(alert_api, "now", lambda: AT + timedelta(seconds=1))
    first = client.post(ack, headers=headers).json()
    monkeypatch.setattr(alert_api, "now", lambda: AT + timedelta(seconds=2))
    later = client.post(ack, headers=headers).json()
    assert first["acknowledged_at"] and later["acknowledged_at"] == first["acknowledged_at"]
    assert later["resolved_at"] is None
    assert active.id in {row["id"] for row in client.get(path, headers=headers).json()["items"]}
    unread = client.get(path, params={"state": "unread"}, headers=headers).json()
    assert {row["id"] for row in unread["items"]} == {recovered.id, event.id}
    assert unread["unread_count"] == 2


def test_chat_uses_alert_evidence_and_only_previews_pump_action(sessions):
    with sessions.begin() as db:
        empty_plan = Plan(
            intents=["alerts"], weather_minutes=None, pump_action=None, pump_seconds=None
        )
        empty = execute_plan(empty_plan, read_snapshot(db, empty_plan, AT, mqtt_connected=True), AT)
        assert empty[0].answer.startswith("Hiện chưa có cảnh báo")
        lost = alert(evidence={"reason": "mqtt_unavailable"})
        db.add_all([lost, alert("sensor_stale", resolved_at=AT)])
    plan = Plan(
        intents=["connection_alerts"],
        weather_minutes=None,
        pump_action=None,
        pump_seconds=None,
    )
    with sessions() as db:
        rows = execute_plan(plan, read_snapshot(db, plan, AT, mqtt_connected=False), AT)
        assert "Backend mất kết nối MQTT" in rows[0].answer and "17:20" in rows[0].answer
        assert [(source.ref, source.timestamp) for source in rows[0].sources] == [(lost.id, AT)]
        pump = Plan(
            intents=["connection_alerts", "irrigation", "pump"],
            weather_minutes=None,
            pump_action="on",
            pump_seconds=10,
        )
        preview = execute_plan(pump, read_snapshot(db, pump, AT, mqtt_connected=False), AT)
        assert len(preview) == 1 and preview[0].status == "read_only" and not preview[0].sources
        assert db.get(AgentAlert, lost.id).acknowledged_at is None
        assert db.scalar(select(Command)) is None
