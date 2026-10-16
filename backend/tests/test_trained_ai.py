from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app.core.settings import settings
from app.integrations.weather import normalize_snapshot, weather_inputs
from app.modules.trained_ai import (
    ROOT,
    InferenceUnavailable,
    artifact,
    model_metadata,
    predict,
    predictor,
)


def test_missing_model_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "leaf_model_dir", str(tmp_path))
    with pytest.raises(InferenceUnavailable, match="chưa khả dụng"):
        model_metadata("leaf")


def test_corrupt_weights_are_rejected_before_loading(monkeypatch, tmp_path):
    import json

    (tmp_path / "metadata.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "smoke_only": False,
                "model_version": "broken",
                "weights_sha256": "invalid",
            }
        )
    )
    for name in ("best.pt", "class_mapping.json", "preprocessing.json"):
        (tmp_path / name).write_bytes(b"bad")
    monkeypatch.setattr(settings, "leaf_model_dir", str(tmp_path))
    with pytest.raises(InferenceUnavailable, match="Checksum"):
        predictor("leaf", "broken")


@pytest.mark.parametrize("task", ["leaf", "fruit", "weather"])
def test_real_artifacts_on_cpu(task):
    if not (artifact(task) / "metadata.json").is_file():
        pytest.skip("Artifact local không được lưu trong Git")
    image = BytesIO()
    Image.new("RGB", (320, 240), "green").save(image, "JPEG")
    inputs = {
        "as_of": "2026-01-01T05:00:00+00:00",
        "weather_source": "open_meteo_archive",
        "hourly": [
            {
                "time": f"2026-01-01T{hour:02}:00:00+00:00",
                "temperature_c": 25,
                "humidity_pct": 80,
                "surface_pressure_hpa": 1000,
                "cloud_cover_pct": 70,
                "wind_speed_ms": 2,
            }
            for hour in range(6)
        ],
    }
    result = predict(task, inputs, image.getvalue(), source="test_fixture")
    assert result["provider"] == "trained" and result["is_mock"] is False
    assert result["model_version"] == model_metadata(task)["model_version"]
    assert result["predicted_at"] and result["input_source"] == "test_fixture"
    if task == "leaf":
        assert len(result["top_k"]) == 3
        assert 0 <= result["confidence"] <= 1
    elif task == "fruit":
        # A blank test image is allowed to have zero detections.
        assert result["count"] == len(result["detections"])
        for detection in result["detections"]:
            assert all(0 <= value <= 1 for value in detection["box"])
    else:
        assert 0 <= result["rain_probability"] <= 1
        assert result["historical_replay"] is True
        assert result["threshold"] != 0.5
        with pytest.raises(InferenceUnavailable):
            predict(task, {**inputs, "hourly": inputs["hourly"][1:]})
        with pytest.raises(InferenceUnavailable):
            predict(task, {**inputs, "as_of": "2026-01-01T04:00:00+00:00"})


def test_openweather_rejects_sea_level_pressure_and_stale_data():
    at = datetime(2026, 1, 1, tzinfo=UTC)
    payload = {
        "dt": at.timestamp(),
        "main": {"temp": 25, "humidity": 70, "pressure": 1010},
        "clouds": {"all": 50},
        "wind": {"speed": 2},
    }
    with pytest.raises(ValueError, match="áp suất mặt đất"):
        normalize_snapshot(payload, at)
    payload["main"]["grnd_level"] = 1000
    assert normalize_snapshot(payload, at)[1]["surface_pressure_hpa"] == 1000
    with pytest.raises(ValueError, match="quá cũ"):
        normalize_snapshot(payload, at + timedelta(minutes=16))


def test_weather_requires_six_causal_observations(monkeypatch):
    monkeypatch.setattr(settings, "openweather_api_key", "test")
    monkeypatch.setattr(settings, "weather_latitude", "21")
    monkeypatch.setattr(settings, "weather_longitude", "105")
    monkeypatch.setattr(settings, "weather_allow_domain_shift", True)
    at = datetime(2026, 1, 1, 6, 5, tzinfo=UTC)
    rows = [
        SimpleNamespace(
            measured_at=at.replace(minute=0) - timedelta(hours=h, minutes=5),
            received_at=at.replace(minute=0) - timedelta(hours=h, minutes=4),
            source="openweather_current",
            readings={},
        )
        for h in range(6)
    ]
    db = SimpleNamespace(scalars=lambda _: rows)
    assert len(weather_inputs(db, at)["snapshots"]) == 6
    rows[0].received_at = at  # Received after forecast origin: must not leak into inputs.
    with pytest.raises(InferenceUnavailable, match="6 mốc giờ"):
        weather_inputs(db, at)
    with pytest.raises(InferenceUnavailable, match="15 phút"):
        weather_inputs(db, at + timedelta(minutes=20))


def test_api_import_does_not_load_torch():
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-c", "import sys; import app.main; assert 'torch' not in sys.modules"],
        check=True,
        cwd=ROOT / "backend",
    )


def test_yolo_boxes_match_original_image_coordinates():
    path = artifact("fruit") / "inference-tomato.jpg"
    if not path.is_file():
        pytest.skip("Ảnh kiểm chứng local không lưu Git")
    result = predict("fruit", {}, path.read_bytes(), source="test_fixture")
    assert result["count"] > 0
    for detection in result["detections"]:
        x, y, width, height = detection["box"]
        x1, y1, x2, y2 = detection["bbox_xyxy"]
        assert x == pytest.approx(x1 / result["image_width"], abs=1e-6)
        assert y == pytest.approx(y1 / result["image_height"], abs=1e-6)
        assert width == pytest.approx((x2 - x1) / result["image_width"], abs=1e-6)
        assert height == pytest.approx((y2 - y1) / result["image_height"], abs=1e-6)
        assert detection["class_name"] in {"ripe", "unripe"}


def test_inference_timeout_terminates_child():
    from app.workers.runtime import InferenceProcess

    terminated, closed = [], []
    runtime = InferenceProcess()
    runtime.process = SimpleNamespace(
        is_alive=lambda: not terminated,
        terminate=lambda: terminated.append(True),
        join=lambda **_: None,
    )
    runtime.connection = SimpleNamespace(
        send=lambda _: None,
        poll=lambda _: False,
        close=lambda: closed.append(True),
    )
    with pytest.raises(TimeoutError):
        runtime.predict("leaf", {}, b"image")
    assert terminated and closed
    assert runtime.process is None and runtime.connection is None
