"""Cấu hình Ultralytics riêng cho bài toán, tắt tích hợp upload ngoài."""

import os
from pathlib import Path


def load_yolo():
    config_root = Path(os.environ.setdefault("YOLO_CONFIG_DIR", str(Path(__file__).resolve().parents[1] / ".runtime")))
    config_root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    from ultralytics import YOLO, settings

    settings.update({key: False for key in ("sync", "hub", "wandb", "mlflow", "comet", "clearml",
                     "neptune", "tensorboard", "dvc", "raytune") if key in settings})
    return YOLO
