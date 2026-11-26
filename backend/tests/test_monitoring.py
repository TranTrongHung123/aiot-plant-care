from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.core.settings import Settings
from app.db.models import Command, Job, PlantImage
from app.modules.monitoring import (
    Sample,
    Snapshot,
    command_evidence,
    evaluate_conditions,
    leaf_evidence,
)

AT = datetime(2026, 11, 22, 10, 20, tzinfo=UTC)


def device(**overrides):
    return replace(Snapshot("test-device", "simulation", AT, True, True, 30, ()), **overrides)


def sample(seconds_ago, soil=22):
    return Sample(str(seconds_ago), "simulation", AT - timedelta(seconds=seconds_ago), soil)


def test_connection_and_stale_boundary():
    unknown = evaluate_conditions(device(last_seen=None, online=False), AT)
    assert not unknown.opening and not unknown.recovered
    assert not evaluate_conditions(device(last_seen=AT - timedelta(seconds=44)), AT).opening
    for connected, reason in [(True, "status_expired"), (False, "mqtt_unavailable")]:
        offline = evaluate_conditions(
            device(
                last_seen=AT - timedelta(seconds=45),
                mqtt_connected=connected,
                samples=(sample(40),),
            ),
            AT,
        )
        assert list(offline.opening) == ["device_offline"]
        assert offline.opening["device_offline"].details["reason"] == reason
    assert evaluate_conditions(device(online=False), AT).opening["device_offline"].details == {
        "reason": "offline_reported"
    }
    stale = evaluate_conditions(device(samples=(sample(30),)), AT)
    assert stale.opening["sensor_stale"].observed_at == sample(30).at
    recovery = evaluate_conditions(device(samples=(sample(0),)), AT)
    assert recovery.recovered["sensor_stale"] == AT
    assert recovery.recovered["device_offline"] == AT


def test_dry_condition_stops_at_gap_missing_value_and_requires_two_wet_samples():
    dry = tuple(sample(age) for age in range(0, 121, 5))
    alert = evaluate_conditions(device(samples=dry), AT).opening["low_soil"]
    assert alert.details["duration_seconds"] == 120 and alert.details["threshold"] == 30
    for samples in (
        dry[:1],
        dry[:3] + dry[8:],
        (dry[0], sample(5, None), *dry[2:]),
        tuple(sample(age) for age in range(30, 155, 5)),
    ):
        assert "low_soil" not in evaluate_conditions(device(samples=samples), AT).opening
    for samples in (
        (sample(0, 34),),
        (sample(0, 34), sample(5, 32)),
        (sample(0, 34), sample(30, 35)),
        (sample(0, None), sample(5, 36)),
    ):
        assert "low_soil" not in evaluate_conditions(device(samples=samples), AT).recovered
    assert (
        evaluate_conditions(device(samples=(sample(0, 34), sample(5, 33))), AT).recovered[
            "low_soil"
        ]
        == AT
    )


def test_timed_out_command_and_late_ack_use_distinct_timestamps():
    failed, recovered = AT + timedelta(seconds=5), AT + timedelta(seconds=8)
    command = Command(
        id="test-command",
        source="simulation",
        action="pump_on",
        status="completed",
        updated_at=recovered,
        events=[
            {"status": "timed_out", "reason": "internal/path", "at": failed.isoformat()},
            {"status": "completed", "at": recovered.isoformat()},
        ],
    )
    evidence, completed = command_evidence(command, AT)
    assert evidence.observed_at == failed and completed == recovered
    assert evidence.details == {"action": "pump_on", "status": "timed_out"}
    assert command_evidence(command, recovered) is None
    command.status, command.events = "failed", []
    assert command_evidence(command, AT)[0].details["status"] == "failed"


def test_leaf_rule_accepts_only_trained_completed_known_class_with_threshold():
    image = PlantImage(id="test-image", source="simulation", purpose="leaf", created_at=AT)
    result = {"is_mock": False, "class_name": "Tomato___Early_blight", "confidence": 0.8}
    job = Job(
        id="test-job",
        task="leaf",
        provider="trained",
        status="completed",
        model_version="v1",
        finished_at=AT,
        result=result,
    )
    assert leaf_evidence(job, image, 0.8).details["label"] == "Đốm lá sớm"
    for field, value in [
        ("confidence", 0.799),
        ("confidence", True),
        ("class_name", "Tomato___healthy"),
        ("class_name", "unknown"),
        ("is_mock", True),
    ]:
        job.result = result | {field: value}
        assert leaf_evidence(job, image, 0.8) is None
    job.status, job.result = "failed", result
    assert leaf_evidence(job, image, 0.8) is None


def test_monitoring_settings_defaults_and_validation(monkeypatch):
    defaults = {
        "monitoring_interval_seconds": 5,
        "monitoring_offline_seconds": 45,
        "monitoring_stale_seconds": 30,
        "monitoring_dry_seconds": 120,
        "monitoring_cooldown_seconds": 60,
        "monitoring_leaf_score": 0.8,
        "monitoring_leaf_cooldown_minutes": 15,
    }
    for field in defaults:
        monkeypatch.delenv(field.upper(), raising=False)
    secrets = {
        "postgres_password": "test-password",
        "minio_app_user": "test-app",
        "minio_app_password": "test-password",
        "app_token": "monitoring-test-token",
    }
    configured = Settings(_env_file=None, **secrets)
    assert configured.model_dump(include=set(defaults)) == defaults
    for field, invalid in (
        ("monitoring_interval_seconds", 0),
        ("monitoring_offline_seconds", 15),
        ("monitoring_stale_seconds", 20),
        ("monitoring_dry_seconds", 1),
        ("monitoring_cooldown_seconds", -1),
        ("monitoring_leaf_score", 1.1),
        ("monitoring_leaf_cooldown_minutes", 0),
    ):
        with pytest.raises(ValidationError, match=field):
            Settings(_env_file=None, **secrets, **{field: invalid})
