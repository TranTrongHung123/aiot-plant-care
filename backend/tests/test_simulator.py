import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.schemas import DEVICE_ID, PREFIX
from app.db.models import now
from app.workers.simulator import Simulator


@pytest.fixture
def simulator(tmp_path):
    sim = Simulator(tmp_path / "device.sqlite3")
    sent = []
    sim.publish = lambda suffix, data, retain=False: sent.append((suffix, data))
    yield sim, sent
    sim.db.close()


def deliver(sim, **changes):
    data = {
        "schema_version": 1,
        "command_id": str(uuid4()),
        "device_id": DEVICE_ID,
        "source": "simulation",
        "action": "pump_on",
        "duration_ms": 5000,
        "expires_at": (now() + timedelta(seconds=10)).isoformat(),
        **changes,
    }
    sim.message(
        None,
        None,
        SimpleNamespace(
            topic=f"{PREFIX}/{DEVICE_ID}/command", payload=json.dumps(data).encode(), retain=False
        ),
    )
    return data


def test_duplicate_does_not_extend_pump_and_stop_has_priority(simulator):
    sim, sent = simulator
    command = deliver(sim)
    deadline = sim.deadline
    deliver(sim, **command)
    assert sim.deadline == deadline
    assert sim.active == command["command_id"]
    deliver(sim, action="pump_off")
    assert sim.active is None
    assert any(data.get("status") == "completed" for _, data in sent)


def test_expired_and_oversized_commands_are_rejected(simulator):
    sim, sent = simulator
    deliver(sim, expires_at=(now() - timedelta(seconds=1)).isoformat())
    deliver(sim, duration_ms=16000)
    assert sim.active is None
    assert [data["status"] for suffix, data in sent if suffix == "ack"] == ["rejected", "rejected"]


def test_live_namespace_payload_and_offline_commands_do_not_activate(simulator):
    sim, _ = simulator
    deliver(sim, source="live")
    assert sim.active is None
    sim.scenario = "offline"
    deliver(sim)
    assert sim.active is None


def test_restart_remembers_command_and_never_resumes_pump(tmp_path):
    path = tmp_path / "state.sqlite3"
    sim = Simulator(path)
    sim.publish = lambda *args, **kwargs: None
    command = deliver(sim)
    sim.db.close()
    restarted = Simulator(path)
    restarted.publish = lambda *args, **kwargs: None
    try:
        deliver(restarted, **command)
        assert restarted.active is None
    finally:
        restarted.db.close()


def test_malformed_retained_and_wrong_topic_messages_cannot_start_pump(simulator):
    sim, sent = simulator
    for payload in [b"[]", b"null", b"not-json", b"{}", b"x" * 16385]:
        sim.message(
            None,
            None,
            SimpleNamespace(
                topic=f"{PREFIX}/{DEVICE_ID}/command",
                payload=payload,
                retain=False,
            ),
        )
    valid = {
        "schema_version": 1,
        "command_id": str(uuid4()),
        "device_id": DEVICE_ID,
        "source": "simulation",
        "action": "pump_on",
        "duration_ms": 1000,
        "expires_at": (now() + timedelta(seconds=10)).isoformat(),
    }
    for topic, retained in [
        (f"{PREFIX}/{DEVICE_ID}/command", True),
        (f"plantcare/v1/live/{DEVICE_ID}/command", False),
    ]:
        sim.message(
            None,
            None,
            SimpleNamespace(
                topic=topic,
                payload=json.dumps(valid).encode(),
                retain=retained,
            ),
        )
    assert sim.active is None and not sent
    deliver(sim, command_id={"bad": "id"})
    assert sim.active is None and not sent
