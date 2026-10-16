"""Load artifact một lần trong worker; không có phụ thuộc DB/HTTP/MQTT."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import torch

from .data import CLASSES
from .model import build_model, make_transform, open_rgb


class Predictor:
    def __init__(self, artifact_dir, device=None):
        root = Path(artifact_dir)
        self.metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        mapping = json.loads((root / "class_mapping.json").read_text(encoding="utf-8"))
        if mapping != {name: i for i, name in enumerate(CLASSES)}:
            raise ValueError("Class mapping không khớp phiên bản code")
        if self.metadata["architecture"] != "mobilenet_v3_large" or self.metadata["num_classes"] != 10:
            raise ValueError("Kiến trúc artifact không được hỗ trợ")
        digest = hashlib.sha256((root / "best.pt").read_bytes()).hexdigest()
        if digest != self.metadata["weights_sha256"]:
            raise ValueError("Checksum weights không khớp")
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.model = build_model(pretrained=False)
        self.model.load_state_dict(torch.load(root / "best.pt", map_location="cpu", weights_only=True))
        self.model.to(self.device).eval()
        self.transform = make_transform(config=json.loads((root / "preprocessing.json").read_text(encoding="utf-8")))

    @torch.inference_mode()
    def predict(self, image_path, *, source="manual", top_k=3):
        if not 1 <= top_k <= 10:
            raise ValueError("top_k phải thuộc [1, 10]")
        tensor = self.transform(open_rgb(image_path)).unsqueeze(0).to(self.device)
        probabilities = self.model(tensor).softmax(1)[0]
        values, indices = probabilities.topk(top_k)
        result = [{"class_name": CLASSES[i], "confidence": float(value)}
                  for value, i in zip(values.cpu().tolist(), indices.cpu().tolist())]
        return {"model_version": self.metadata["model_version"], "source": source,
                "model_source": "trained", "smoke_only": self.metadata["smoke_only"],
                "predicted_at": datetime.now(timezone.utc).isoformat(),
                "label": result[0]["class_name"], "confidence": result[0]["confidence"], "top_k": result}
