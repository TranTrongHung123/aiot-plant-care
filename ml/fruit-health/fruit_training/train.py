"""Train YOLO11n, lưu best theo Validation mAP50-95, tách Test khỏi training."""

import importlib.metadata
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

from .data import NAMES, sha256, verify_prepared, write_json
from .runtime import load_yolo


@dataclass(frozen=True)
class TrainConfig:
    epochs: int = 80
    imgsz: int = 640
    batch: int = 16
    workers: int = 2
    patience: int = 15
    seed: int = 42
    confidence: float = 0.25
    nms_iou: float = 0.7
    max_det: int = 300
    smoke: bool = False


def train(data_yaml, run_dir, *, config=None, resume=False, pretrained_path=None):
    config = config or TrainConfig()
    if config.epochs < 1 or config.imgsz < 64 or config.imgsz % 32 or config.batch < 1:
        raise ValueError("Cấu hình epoch/batch/imgsz không hợp lệ; imgsz phải chia hết 32")
    if not 0 < config.confidence < 1 or not 0 < config.nms_iou < 1 or config.max_det < 1:
        raise ValueError("Cấu hình inference không hợp lệ")
    manifest = verify_prepared(data_yaml)
    if config.smoke and any(r["split"] == "test" for r in manifest["records"]):
        raise ValueError("Prepared smoke không được chứa Test")
    run_dir, data_yaml = Path(run_dir).resolve(), Path(data_yaml).resolve()
    config_path = run_dir / "run_config.json"
    identity = {"config": asdict(config), "data_yaml": str(data_yaml), "fingerprint": manifest["fingerprint"]}
    if resume:
        if json.loads(config_path.read_text(encoding="utf-8")) != identity:
            raise ValueError("Resume cần cùng cấu hình, đường dẫn prepared dataset và fingerprint")
        if (run_dir / "metadata.json").exists():
            raise ValueError("Run đã xuất artifact hoàn tất; không resume run đã kết thúc")
    elif run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError("Run đã tồn tại; chọn tên mới hoặc resume run đang dở")
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(config_path, identity)
    write_json(run_dir / "dataset_manifest.json", manifest)
    YOLO = load_yolo()
    device = 0 if torch.cuda.is_available() else "cpu"
    if resume:
        checkpoint = run_dir / "yolo" / "weights" / "last.pt"
        if not checkpoint.is_file():
            raise FileNotFoundError("Thiếu checkpoint last.pt để resume")
        model = YOLO(str(checkpoint), task="detect")
        model.train(resume=str(checkpoint), device=device)
    else:
        checkpoint = Path(pretrained_path) if pretrained_path else Path(__file__).resolve().parents[1] / "checkpoints" / "yolo11n.pt"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        model = YOLO(str(checkpoint), task="detect")
        if model.task != "detect":
            raise ValueError("Cần YOLO detection pretrained")
        model.train(data=str(data_yaml), project=str(run_dir), name="yolo", exist_ok=False,
            epochs=config.epochs, imgsz=config.imgsz, batch=config.batch, workers=config.workers,
            patience=config.patience, seed=config.seed, deterministic=True, device=device,
            amp=device != "cpu", optimizer="AdamW", lr0=0.001, weight_decay=0.0005,
            val=True, split="val", save=True, save_period=-1, plots=True, cache=False,
            hsv_h=0.0, hsv_s=0.2, hsv_v=0.2, degrees=5.0, translate=0.1, scale=0.2,
            fliplr=0.5, flipud=0.0, mosaic=0.25, close_mosaic=10 if config.epochs > 10 else 0,
            mixup=0.0, copy_paste=0.0, max_det=config.max_det)
    best = run_dir / "yolo" / "weights" / "best.pt"
    if not best.is_file():
        raise FileNotFoundError("Training chưa tạo best.pt; không xuất model giả")
    shutil.copyfile(best, run_dir / "best.pt")
    write_json(run_dir / "class_mapping.json", {str(i): name for i, name in enumerate(NAMES)})
    preprocessing = {"imgsz": config.imgsz, "color": "RGB", "resize": "letterbox",
                     "rect": False, "scale": 1 / 255, "confidence": config.confidence,
                     "nms_iou": config.nms_iou, "max_det": config.max_det,
                     "box_format": "xyxy", "normalized_box_range": [0, 1]}
    write_json(run_dir / "preprocessing.json", preprocessing)
    write_json(run_dir / "metadata.json", {
        "schema_version": 1, "model_version": run_dir.name, "architecture": "yolo11n",
        "task": "fruit_ripeness_detection", "model_source": "trained", "is_mock": False,
        "smoke_only": config.smoke, "production_ready": False, "names": NAMES,
        "created_at": datetime.now(timezone.utc).isoformat(), "training_config": asdict(config),
        "selection_metric": "validation_mAP50_95", "dataset_fingerprint": manifest["fingerprint"],
        "source_url": "https://universe.roboflow.com/uni-zkvyc/tomato-ripeness-um0yl/dataset/4",
        "source_label_conversion": "YOLO polygons to enclosing axis-aligned boxes",
        "environment": {name: importlib.metadata.version(name) for name in ("torch", "torchvision", "ultralytics", "numpy")},
        "artifact_sha256": {name: sha256(run_dir / name) for name in ("best.pt", "class_mapping.json", "preprocessing.json")},
        "limitations": ["Chưa đánh giá ảnh ESP32-CAM", "Chỉ có ripe/unripe, không nhận diện bệnh quả",
                        "Đếm trong từng ảnh, không tracking danh tính quả qua thời gian",
                        "Raw dataset đã stretch 640x640; ảnh camera có domain shift"],
    })
    return run_dir


def evaluate(data_yaml, artifact_dir, *, split="val", allow_test=False):
    from .inference import FruitPredictor

    if split not in {"val", "test"}:
        raise ValueError("Chỉ đánh giá val/test")
    manifest = verify_prepared(data_yaml)
    artifact_dir, root = Path(artifact_dir), Path(data_yaml).resolve().parent
    predictor = FruitPredictor(artifact_dir)
    if manifest["fingerprint"] != predictor.metadata["dataset_fingerprint"]:
        raise ValueError("Dataset khác manifest của model")
    if split == "test":
        if not allow_test or predictor.metadata["smoke_only"]:
            raise ValueError("Test chỉ mở sau khi chốt thí nghiệm đầy đủ; không dùng cho smoke")
        if (artifact_dir / "test_metrics.json").exists():
            raise FileExistsError("Test đã được đánh giá; đọc báo cáo đã lưu")
    rows = [r for r in manifest["records"] if r["split"] == split]
    if not rows:
        raise ValueError("Tập đánh giá rỗng")
    config = predictor.preprocessing
    metric = predictor.model.val(data=str(Path(data_yaml).resolve()), split=split,
        imgsz=config["imgsz"], batch=4, device=predictor.device, workers=0, plots=True,
        conf=0.001, iou=config["nms_iou"], max_det=config["max_det"],
        project=str(artifact_dir / "evaluation"), name=split, exist_ok=True)
    per_class = {}
    for position, class_id in enumerate(metric.box.ap_class_index):
        precision, recall, ap50, ap = metric.box.class_result(position)
        per_class[NAMES[int(class_id)]] = {"precision": float(precision), "recall": float(recall),
                                         "mAP50": float(ap50), "mAP50_95": float(ap)}
    predictions, total_errors, class_errors = [], [], []
    for row in rows:
        prediction = predictor.predict(root / row["prepared_image"], source="roboflow_evaluation")
        actual = [sum(int(box[0]) == i for box in row["boxes"]) for i in range(2)]
        estimated = [prediction["counts_by_class"][name] for name in NAMES]
        total_errors.append(sum(estimated) - sum(actual))
        class_errors.append(np.array(estimated) - np.array(actual))
        predictions.append({"path": row["path"], "target_counts": dict(zip(NAMES, actual)), **prediction})
    errors = np.array(total_errors)
    result = {"model_version": predictor.metadata["model_version"], "split": split,
              "evaluated_at": datetime.now(timezone.utc).isoformat(), "images": len(rows),
              "mAP50": float(metric.box.map50), "mAP50_95": float(metric.box.map),
              "precision": float(metric.box.mp), "recall": float(metric.box.mr), "per_class": per_class,
              "count_confidence": config["confidence"], "nms_iou": config["nms_iou"],
              "count_mae": float(np.abs(errors).mean()), "count_rmse": float(np.sqrt((errors ** 2).mean())),
              "count_bias": float(errors.mean()),
              "class_count_mae": dict(zip(NAMES, np.abs(class_errors).mean(axis=0).tolist())),
              "max_det": config["max_det"], "precision_recall_note": "P/R là thống kê tại điểm F1 tốt nhất trên tập đang đánh giá; không dùng để đổi ngưỡng. Count dùng confidence cố định từ artifact."}
    write_json(artifact_dir / f"{split}_metrics.json", result)
    write_json(artifact_dir / f"{split}_predictions.json", predictions)
    return result
