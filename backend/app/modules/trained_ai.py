"""Adapter inference CPU. Chỉ worker import thư viện ML và nạp weights."""

import hashlib
import io
import json
import sys
from functools import lru_cache
from pathlib import Path

from app.core.settings import settings

ROOT = Path(__file__).resolve().parents[3]
TARGETS = {"leaf": "MobileNetV3", "fruit": "YOLO", "weather": "XGBoost"}
LEAF_LABELS = {
    "Tomato___Bacterial_spot": "Đốm vi khuẩn",
    "Tomato___Early_blight": "Đốm lá sớm",
    "Tomato___Late_blight": "Mốc sương",
    "Tomato___Leaf_Mold": "Mốc lá",
    "Tomato___Septoria_leaf_spot": "Đốm lá Septoria",
    "Tomato___Spider_mites Two-spotted_spider_mite": "Nhện đỏ hai chấm",
    "Tomato___Target_Spot": "Đốm vòng",
    "Tomato___Tomato_Yellow_Leaf_Curl_Virus": "Virus xoăn vàng lá",
    "Tomato___Tomato_mosaic_virus": "Virus khảm cà chua",
    "Tomato___healthy": "Lá khỏe",
}


class InferenceUnavailable(ValueError):
    pass


def artifact(task):
    if task not in TARGETS:
        raise InferenceUnavailable("Bài toán AI không được hỗ trợ")
    root = Path(getattr(settings, f"{task}_model_dir"))
    return root if root.is_absolute() else ROOT / root


def model_metadata(task):
    try:
        root = artifact(task)
        metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        if metadata.get("schema_version") != 1 or metadata.get("smoke_only", True):
            raise ValueError("Artifact sai schema hoặc chỉ dùng smoke test")
        if not isinstance(metadata.get("model_version"), str) or not metadata["model_version"]:
            raise ValueError("Artifact thiếu model_version")
        files = (
            ["model.ubj", "calibration.json", "feature_config.json"]
            if task == "weather"
            else ["best.pt", "class_mapping.json", "preprocessing.json"]
        )
        if not all((root / name).is_file() for name in files):
            raise ValueError("Thiếu weights hoặc cấu hình preprocessing")
        return metadata
    except (OSError, ValueError, KeyError) as exc:
        raise InferenceUnavailable(f"Model {task} chưa khả dụng: {exc}") from exc


def model_status():
    result = {}
    for task in TARGETS:
        try:
            metadata = model_metadata(task)
            result[task] = {"status": "configured", "model_version": metadata["model_version"]}
        except InferenceUnavailable as exc:
            result[task] = {"status": "unavailable", "reason": str(exc)}
    return result


def enable_ml_imports():
    for directory in ("leaf-disease", "fruit-health", "weather"):
        path = str(ROOT / "ml" / directory)
        if path not in sys.path:
            sys.path.insert(0, path)


@lru_cache(maxsize=3)
def predictor(task, version):
    metadata = model_metadata(task)
    if metadata["model_version"] != version:
        raise InferenceUnavailable("Phiên bản model đã đổi; hãy tạo job mới")
    enable_ml_imports()
    root = artifact(task)
    # Verify before deserializing trusted local model files (including YOLO checkpoint).
    checksums = metadata.get("artifact_sha256", {"best.pt": metadata.get("weights_sha256")})
    for name, digest in checksums.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise InferenceUnavailable(f"Checksum model {task} không khớp: {name}")
    if task == "weather":
        from weather_training.inference import RainPredictor

        return RainPredictor(root)
    import torch

    torch.set_num_threads(2)
    if task == "leaf":
        from leaf_training.inference import Predictor

        return Predictor(root, device="cpu")
    from fruit_training.inference import FruitPredictor

    return FruitPredictor(root, device="cpu")


def predict(task, inputs, image_bytes=b"", *, source="simulation", model_version=None):
    version = model_version or model_metadata(task)["model_version"]
    try:
        engine = predictor(task, version)
        if task == "weather":
            import pandas as pd

            if not (inputs.get("hourly") or inputs.get("snapshots")) or not inputs.get("as_of"):
                raise InferenceUnavailable(
                    "Cần lịch sử thời tiết 6 giờ; không dùng telemetry thay thế"
                )
            if inputs.get("snapshots"):
                from weather_training.adapters import snapshots_to_hourly

                hourly = snapshots_to_hourly(inputs["snapshots"], as_of=inputs["as_of"])
            else:
                from weather_training.data import utc_timestamp

                hourly = pd.DataFrame(inputs["hourly"])
                hourly.index = pd.DatetimeIndex([utc_timestamp(t) for t in hourly.pop("time")])
            result = engine.predict(
                hourly,
                as_of=inputs["as_of"],
                source=inputs["weather_source"],
                allow_domain_shift=settings.weather_allow_domain_shift,
            )
            result["label"] = "Có mưa" if result["prediction"] else "Ít mưa"
            result["notice"] = (
                "Phát lại dữ liệu lịch sử, không phải dự báo hiện tại."
                if result["historical_replay"]
                else "Thử nghiệm nguồn OpenWeather; chưa đánh giá sai lệch nguồn."
            )
        else:
            if not image_bytes:
                raise InferenceUnavailable("Job thiếu ảnh đầu vào")
            result = engine.predict(io.BytesIO(image_bytes), source=source)
            result["notice"] = "Model đã huấn luyện; chưa đánh giá trên ảnh ESP32-CAM."
            if task == "fruit":
                for detection in result["detections"]:
                    x1, y1, x2, y2 = detection["bbox_xyxy_normalized"]
                    detection["box"] = [x1, y1, x2 - x1, y2 - y1]
                    detection["class_name"] = detection["label"]
                    detection["label"] = {"ripe": "Chín", "unripe": "Xanh"}[detection["label"]]
                result["label"] = f"{result['count']} quả"
            else:
                result["class_name"] = result["label"]
                result["label"] = LEAF_LABELS[result["label"]]
                result["notice"] += (
                    " Điểm softmax chưa hiệu chuẩn; ảnh ngoài phạm vi vẫn có thể có điểm cao."
                )
        return {
            **result,
            "provider": "trained",
            "is_mock": False,
            "target_model": TARGETS[task],
            "input_source": source,
        }
    except InferenceUnavailable:
        raise
    except (ValueError, OSError, KeyError, ImportError, RuntimeError) as exc:
        raise InferenceUnavailable(f"Không chạy được {TARGETS[task]}: {exc}") from exc
