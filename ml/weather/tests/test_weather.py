"""Kiểm tra cửa sổ nhãn, leakage, artifact và dữ liệu live đến muộn."""

import json

import numpy as np
import pandas as pd
import pytest

from weather_training.adapters import openweather_snapshot, snapshots_to_hourly
from weather_training.data import BASE_COLUMNS, load_open_meteo_csv, validate_hourly
from weather_training.features import FeatureConfig, build_features, chronological_split, make_supervised
from weather_training.inference import RainPredictor
from weather_training.train import TrainConfig, evaluate_test, train


@pytest.fixture
def hourly():
    rng = np.random.default_rng(42)
    index = pd.date_range("2021-01-01", periods=1500, freq="h", tz="UTC", name="time")
    values = rng.random((len(index), 6))
    frame = pd.DataFrame(values, index=index, columns=BASE_COLUMNS + ["precipitation_mm"])
    frame.temperature_c = frame.temperature_c * 15 + 15
    frame.humidity_pct *= 100
    frame.surface_pressure_hpa = frame.surface_pressure_hpa * 20 + 1000
    frame.cloud_cover_pct *= 100
    frame.wind_speed_ms *= 5
    frame.precipitation_mm = np.where(frame.humidity_pct > 65, 0.5, 0.0)
    return frame


def test_target_alignment_and_no_future_features(hourly):
    table, report = make_supervised(hourly)
    timestamp = table.index[50]
    assert table.loc[timestamp, "target"] == (hourly.loc[timestamp + pd.Timedelta(hours=1), "precipitation_mm"] >= 0.1)
    changed = hourly.copy()
    changed.loc[changed.index > timestamp, BASE_COLUMNS] = 0
    original_features = build_features(hourly)
    pd.testing.assert_frame_equal(original_features.loc[:timestamp], build_features(changed).loc[:timestamp])
    assert "precipitation_mm" not in report["feature_names"]
    assert "target" not in report["feature_names"]
    assert hourly.index[-1] not in table.index


def test_missing_hours_never_become_dry_labels(hourly):
    missing = hourly.index[70]
    gapped = hourly.drop(missing)
    table, _ = make_supervised(gapped)
    assert missing - pd.Timedelta(hours=1) not in table.index
    assert missing not in table.index
    assert missing + pd.Timedelta(hours=5) not in table.index
    assert missing + pd.Timedelta(hours=6) in table.index
    clean, audit = validate_hourly(pd.concat([hourly, hourly.iloc[[0]]]))
    assert audit["duplicate_rows_removed"] == 1
    pd.testing.assert_frame_equal(clean, hourly, check_freq=False)
    conflict = hourly.iloc[[0]].copy()
    conflict.temperature_c += 1
    with pytest.raises(ValueError, match="mâu thuẫn"):
        validate_hourly(pd.concat([hourly, conflict]))


def test_chronological_embargo(hourly):
    table, _ = make_supervised(hourly)
    parts = chronological_split(table)
    for left, right in zip(list(parts.values()), list(parts.values())[1:]):
        assert left.target_end.max() < right.index.min() - pd.Timedelta(hours=5)
        assert not set(left.index).intersection(right.index)
    with pytest.raises(ValueError, match="Embargo"):
        chronological_split(table, embargo_hours=0)


def test_csv_timezone_and_units(tmp_path):
    path = tmp_path / "sample.csv"
    path.write_text(
        "latitude,longitude,elevation,utc_offset_seconds,timezone,timezone_abbreviation\n"
        "20.9,105.7,8,25200,Asia/Bangkok,GMT+7\n\n"
        "time,temperature_2m (°C),relative_humidity_2m (%),precipitation (mm),"
        "surface_pressure (hPa),cloud_cover (%),wind_speed_10m (km/h)\n"
        "2021-01-01T00:00,20,70,0.1,1010,50,3.6\n", encoding="utf-8")
    frame, source = load_open_meteo_csv(path)
    assert frame.index[0] == pd.Timestamp("2020-12-31T17:00Z")
    assert frame.wind_speed_ms.iloc[0] == 1.0
    assert source["audit"]["rows"] == 1


def test_openweather_semantics_and_late_arrivals():
    reference = pd.Timestamp("2026-01-01T10:00Z")
    payload = {"dt": int((reference - pd.Timedelta(minutes=5)).timestamp()),
               "main": {"temp": 25, "humidity": 80, "grnd_level": 1008, "pressure": 1015},
               "clouds": {"all": 90}, "wind": {"speed": 2}}
    snapshot = openweather_snapshot(payload, received_at=reference, units="metric")
    assert snapshot["surface_pressure_hpa"] == 1008
    with pytest.raises(ValueError, match="metric"):
        openweather_snapshot(payload, received_at=reference, units="standard")
    del payload["main"]["grnd_level"]
    with pytest.raises(ValueError, match="grnd_level"):
        openweather_snapshot(payload, received_at=reference, units="metric")
    snapshots = [{**snapshot, "measured_at": (reference - pd.Timedelta(hours=i, minutes=5)).isoformat(),
                   "received_at": (reference - pd.Timedelta(hours=i, minutes=1)).isoformat()} for i in range(6)]
    assert len(snapshots_to_hourly(snapshots, as_of=reference)) == 6
    snapshots[0]["received_at"] = (reference + pd.Timedelta(minutes=1)).isoformat()
    with pytest.raises(ValueError, match="Thiếu snapshot"):
        snapshots_to_hourly(snapshots, as_of=reference)


def test_train_export_inference_and_test_guard(hourly, tmp_path):
    table, report = make_supervised(hourly)
    run = train(table, report["feature_names"], tmp_path / "smoke",
                config=TrainConfig(num_boost_round=8, early_stopping_rounds=3, smoke=True))
    predictor = RainPredictor(run)
    reference = hourly.index[100]
    prediction = predictor.predict(hourly.loc[:reference], as_of=reference, source="open_meteo_archive")
    assert 0 <= prediction["rain_probability"] <= 1
    assert prediction["horizon_minutes"] == 60 and prediction["historical_replay"]
    assert prediction["smoke_only"] and not prediction["production_ready"]
    with pytest.raises(ValueError, match="tương lai"):
        predictor.predict(hourly, as_of=reference, source="open_meteo_archive")
    with pytest.raises(ValueError, match="cũ"):
        predictor.predict(hourly.iloc[:90], as_of=reference, source="open_meteo_archive")
    with pytest.raises(ValueError, match="domain shift"):
        predictor.predict(hourly.loc[:reference], as_of=reference, source="openweather_current")
    with pytest.raises(ValueError, match="Test"):
        evaluate_test(table, run, allow_test=True)
    with pytest.raises(FileExistsError):
        train(table, report["feature_names"], run)
    (run / "calibration.json").write_text("{}")
    with pytest.raises(ValueError, match="Checksum"):
        RainPredictor(run)


def test_final_test_uses_frozen_artifact(hourly, tmp_path):
    table, report = make_supervised(hourly)
    run = train(table, report["feature_names"], tmp_path / "complete",
                feature_config=FeatureConfig(), config=TrainConfig(num_boost_round=3, early_stopping_rounds=2))
    before = (run / "metadata.json").read_bytes()
    with pytest.raises(ValueError, match="Test"):
        evaluate_test(table, run)
    changed = table.copy()
    changed.iloc[0, 0] += 0.1
    with pytest.raises(ValueError, match="manifest"):
        evaluate_test(changed, run, allow_test=True)
    result = evaluate_test(table, run, allow_test=True)
    assert result["xgboost_calibrated"]["rows"] == len(chronological_split(table)["test"])
    assert before == (run / "metadata.json").read_bytes()
    assert json.loads(before)["production_ready"] is False
    with pytest.raises(FileExistsError):
        evaluate_test(table, run, allow_test=True)
