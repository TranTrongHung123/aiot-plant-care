"""Load model một lần trong worker; trả box, nhãn và số quả trong ảnh."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import torch
from PIL import Image, ImageOps

from .data import NAMES, sha256
from .runtime import load_yolo


class FruitPredictor:
    def __init__(self, artifact_dir, device=None):
        root = Path(artifact_dir)
        self.metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        if self.metadata["schema_version"] != 1 or self.metadata["task"] != "fruit_ripeness_detection":
            raise ValueError("Artifact không đúng task/schema")
        for name in ("best.pt", "class_mapping.json", "preprocessing.json"):
            if sha256(root / name) != self.metadata["artifact_sha256"][name]:
                raise ValueError(f"Checksum không khớp: {name}")
        mapping = json.loads((root / "class_mapping.json").read_text(encoding="utf-8"))
        if mapping != {str(i): name for i, name in enumerate(NAMES)}:
            raise ValueError("Class mapping không khớp")
        self.preprocessing = json.loads((root / "preprocessing.json").read_text(encoding="utf-8"))
        self.model = load_yolo()(str(root / "best.pt"), task="detect")
        if self.model.names != dict(enumerate(NAMES)):
            raise ValueError("Mapping trong weights không khớp artifact")
        self.device = device if device is not None else (0 if torch.cuda.is_available() else "cpu")

    def predict(self, image_path, *, source="manual", annotated_path=None):
        with Image.open(image_path) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
        cfg = self.preprocessing
        result = self.model.predict(image, imgsz=cfg["imgsz"], conf=cfg["confidence"],
            iou=cfg["nms_iou"], max_det=cfg["max_det"], device=self.device,
            rect=False, augment=False, verbose=False)[0]
        detections = []
        for xyxy, normalized, confidence, class_id in zip(result.boxes.xyxy.cpu().tolist(),
                result.boxes.xyxyn.cpu().tolist(), result.boxes.conf.cpu().tolist(), result.boxes.cls.cpu().tolist()):
            detections.append({"class_id": int(class_id), "label": NAMES[int(class_id)],
                               "confidence": float(confidence), "bbox_xyxy": xyxy,
                               "bbox_xyxy_normalized": normalized})
        counts = Counter(detection["label"] for detection in detections)
        if annotated_path is not None:
            annotated_path = Path(annotated_path)
            annotated_path.parent.mkdir(parents=True, exist_ok=True)
            result.save(filename=str(annotated_path))
        return {"model_version": self.metadata["model_version"], "model_source": "trained", "is_mock": False,
                "smoke_only": self.metadata["smoke_only"], "production_ready": False, "source": source,
                "predicted_at": datetime.now(timezone.utc).isoformat(), "image_width": image.width,
                "image_height": image.height, "count": len(detections),
                "counts_by_class": {name: counts[name] for name in NAMES}, "detections": detections,
                "count_may_be_truncated": len(detections) >= cfg["max_det"]}
