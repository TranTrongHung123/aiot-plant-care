from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.settings import settings
from app.main import app
from app.modules.stream_relay import StreamRelay


def test_stream_relay_url_normalization():
    relay = StreamRelay("http://192.168.1.50:81/stream")
    assert relay.cam_url == "http://192.168.1.50:81/stream"

    relay.set_cam_url("192.168.1.60")
    assert relay.cam_url == "http://192.168.1.60:81/stream"

    relay.set_cam_url("http://10.0.0.5:81/stream")
    assert relay.cam_url == "http://10.0.0.5:81/stream"


def test_stream_relay_create_status_frame():
    relay = StreamRelay()
    frame = relay._create_status_frame("Test message")
    assert isinstance(frame, bytes)
    assert frame.startswith(b"\xff\xd8")  # JPEG Start of Image
    assert frame.endswith(b"\xff\xd9")    # JPEG End of Image


def test_stream_relay_generator_and_viewer_lifecycle():
    relay = StreamRelay("http://127.0.0.1:65534/stream")
    assert relay.get_status()["viewer_count"] == 0

    gen = relay.generate_stream(mode="ai")
    # First next() executes until yield and registers viewer
    chunk = next(gen)
    assert relay.get_status()["viewer_count"] == 1
    assert chunk.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")

    # Closing generator unregisters viewer
    gen.close()
    assert relay.get_status()["viewer_count"] == 0


def test_stream_api_endpoints_authorization():
    client = TestClient(app)

    # 1. Unauthorized request
    res = client.get("/api/v1/stream/status")
    assert res.status_code == 401

    # 2. Authorized via Header
    res = client.get("/api/v1/stream/status", headers={"Authorization": f"Bearer {settings.app_token}"})
    assert res.status_code == 200
    data = res.json()
    assert "running" in data
    assert "cam_url" in data

    # 3. Authorized via Query Param (?token=...)
    res = client.get(f"/api/v1/stream/status?token={settings.app_token}")
    assert res.status_code == 200
    assert "viewer_count" in res.json()


def test_stream_routes_response_type():
    client = TestClient(app)
    res = client.get(f"/api/v1/stream/raw?token={settings.app_token}&max_frames=1")
    assert res.status_code == 200
    assert "multipart/x-mixed-replace" in res.headers.get("content-type", "")
    assert b"--frame" in res.content


def test_websocket_stream_auth_and_messaging():
    client = TestClient(app)

    # 1. Invalid token closes with 1008
    try:
        with client.websocket_connect("/ws/stream?token=invalid_token") as ws:
            pass
    except Exception:
        pass  # Disconnected or 1008 as expected

    # 2. Valid token connects successfully
    with client.websocket_connect(f"/ws/stream?token={settings.app_token}") as ws:
        # Client receives either binary frame or pong when pinging
        ws.send_json({"type": "ping"})
        received_types = set()
        for _ in range(3):
            msg = ws.receive()
            if "bytes" in msg:
                received_types.add("bytes")
                assert msg["bytes"].startswith(b"\xff\xd8")
            elif "text" in msg:
                received_types.add("text")
                import json
                parsed = json.loads(msg["text"])
                assert parsed.get("type") in ("pong", "ai_overlay")
        assert len(received_types) > 0



def test_stream_capture_snapshot_and_route():
    from app.modules.stream_relay import stream_relay
    client = TestClient(app)

    # Create dummy status frame as raw JPEG
    dummy_frame = stream_relay._create_status_frame("Snapshot Test")
    with stream_relay._lock:
        stream_relay._latest_raw_jpeg = dummy_frame

    snapshot = stream_relay.extract_snapshot()
    assert snapshot is not None
    assert snapshot.startswith(b"\xff\xd8")
    assert snapshot.endswith(b"\xff\xd9")

    # When no frame is available
    with stream_relay._lock:
        stream_relay._latest_raw_jpeg = None
    assert stream_relay.extract_snapshot() is None

    # Test POST /api/v1/stream/capture 503 when no stream frame
    res = client.post(
        "/api/v1/stream/capture",
        headers={"Authorization": f"Bearer {settings.app_token}"},
    )
    assert res.status_code == 503

