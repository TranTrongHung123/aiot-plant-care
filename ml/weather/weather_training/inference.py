"""Inference chỉ trả dự báo; không gửi MQTT hay kích hoạt bơm."""

import hashlib
import json
from pathlib import Path

import pandas as pd
import xgboost as xgb

from .data import utc_timestamp
from .features import FeatureConfig, build_features
from .calibration import calibrate


class RainPredictor:
    def __init__(self, artifact_dir):
        root = Path(artifact_dir)
        self.metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        if self.metadata["schema_version"] != 1 or self.metadata["target"] != "precipitation_next_60m":
            raise ValueError("Artifact không đúng schema/target")
        for name in ("model.ubj", "calibration.json", "feature_config.json"):
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != self.metadata["artifact_sha256"][name]:
                raise ValueError(f"Checksum không khớp: {name}")
        self.calibration = json.loads((root / "calibration.json").read_text(encoding="utf-8"))
        self.config = FeatureConfig(**json.loads((root / "feature_config.json").read_text(encoding="utf-8")))
        self.model = xgb.Booster()
        self.model.load_model(root / "model.ubj")
        self.model.set_param({"device": "cpu", "nthread": 2})

    def predict(self, hourly, *, as_of, source, allow_domain_shift=False):
        """as_of là mốc dự báo trên lưới giờ, không tự thay bằng giờ mới nhất trong dữ liệu."""
        reference = utc_timestamp(as_of)
        if reference != reference.floor("h"):
            raise ValueError("as_of phải căn đúng giờ để giữ horizon 60 phút đã huấn luyện")
        if source not in {"open_meteo_archive", "openweather_current"}:
            raise ValueError("Nguồn chưa được hỗ trợ; không thay bằng cảm biến/simulation thiếu đặc trưng")
        if source == "openweather_current" and not allow_domain_shift:
            raise ValueError("Chưa đánh giá domain shift; chỉ bật allow_domain_shift cho thử nghiệm")
        if not isinstance(hourly.index, pd.DatetimeIndex) or hourly.index.tz is None:
            raise ValueError("Lịch sử cần timestamp có timezone")
        if (hourly.index > reference).any():
            raise ValueError("Input chứa dữ liệu tương lai so với as_of")
        if hourly.empty or hourly.index.max() != reference:
            raise ValueError("Dữ liệu cũ/thiếu giờ as_of; không trả dự đoán giả")
        if source == "openweather_current":
            age = pd.Timestamp.now(tz="UTC") - reference
            if not pd.Timedelta(0) <= age <= pd.Timedelta(minutes=15):
                raise ValueError("Mốc dự báo live đã cũ hoặc nằm trong tương lai")
            if not {"available_at", "measured_at"}.issubset(hourly.columns):
                raise ValueError("Input live thiếu thời điểm đo/nhận")
            for timestamp, row in hourly.iterrows():
                measured, received = utc_timestamp(row["measured_at"]), utc_timestamp(row["available_at"])
                if not measured <= received <= timestamp:
                    raise ValueError("Snapshot chưa sẵn có tại mốc giờ đặc trưng")
                if timestamp - measured > pd.Timedelta(minutes=15):
                    raise ValueError("Snapshot quá cũ")
        features = build_features(hourly, self.config).loc[[reference]]
        if features.isna().any(axis=None):
            raise ValueError("Cần đủ 6 giờ liên tiếp với 5 biến hợp lệ; không nội suy tương lai")
        if list(features.columns) != self.metadata["feature_names"]:
            raise ValueError("Schema đặc trưng không khớp artifact")
        raw = self.model.predict(xgb.DMatrix(features))
        probability = float(calibrate(raw, self.calibration)[0])
        return {
            "model_version": self.metadata["model_version"], "model_source": "trained", "is_mock": False,
            "source": source, "smoke_only": self.metadata["smoke_only"], "production_ready": False,
            "target": "precipitation_next_60m", "rain_proxy": True,
            "rain_probability": probability, "threshold": self.metadata["threshold"],
            "prediction": int(probability >= self.metadata["threshold"]),
            "forecast_start": reference.isoformat(), "forecast_end": (reference + pd.Timedelta(hours=1)).isoformat(),
            "horizon_minutes": 60, "predicted_at": pd.Timestamp.now(tz="UTC").isoformat(),
            "domain_shift": source != "open_meteo_archive",
            "historical_replay": source == "open_meteo_archive",
        }
