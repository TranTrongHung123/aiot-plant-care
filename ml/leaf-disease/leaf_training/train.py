"""Huấn luyện hai giai đoạn, resume theo epoch và chọn best bằng Validation Macro F1."""

import hashlib
import json
import os
import platform
import random
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import torch
import torchvision
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.metrics import precision_recall_fscore_support
from torch.utils.data import DataLoader, Dataset

from .data import CLASSES, assert_no_leakage, write_json
from .model import PREPROCESSING, build_model, make_transform, open_rgb


@dataclass
class TrainConfig:
    seed: int = 42
    epochs: int = 20
    warmup_epochs: int = 2
    batch_size: int = 32
    workers: int = 2
    head_lr: float = 1e-3
    finetune_lr: float = 1e-4
    weight_decay: float = 1e-4
    patience: int = 5
    min_delta: float = 1e-4
    imbalance_threshold: float = 2.0
    amp: bool = True
    pretrained: bool = True
    smoke: bool = False


def seed_everything(seed):
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.use_deterministic_algorithms(True)


def seed_worker(worker_id):
    seed = torch.initial_seed() % 2**32
    random.seed(seed)
    np.random.seed(seed)


class LeafDataset(Dataset):
    def __init__(self, root, records, training=False):
        self.root, self.records = Path(root), records
        self.transform = make_transform(training)

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        row = self.records[index]
        return self.transform(open_rgb(self.root / row["path"])), row["label"]


def make_loader(root, records, config, training, epoch=0):
    return DataLoader(LeafDataset(root, records, training), batch_size=config.batch_size,
                      shuffle=training, num_workers=config.workers,
                      pin_memory=torch.cuda.is_available(), worker_init_fn=seed_worker,
                      generator=torch.Generator().manual_seed(config.seed + epoch))


def metric_summary(targets, predictions):
    precision, recall, f1, _ = precision_recall_fscore_support(
        targets, predictions, labels=list(range(10)), average="macro", zero_division=0)
    return {"accuracy": float(accuracy_score(targets, predictions)),
            "precision_macro": float(precision), "recall_macro": float(recall), "macro_f1": float(f1)}


def run_epoch(model, loader, device, criterion, scaler=None, optimizer=None, frozen=False):
    training = optimizer is not None
    model.train(training)
    if training and frozen:
        model.features.eval()  # Đóng băng cả running statistics của BatchNorm.
    total_loss, total_weight, targets, predictions = 0.0, 0.0, [], []
    for images, labels in loader:
        images, labels = images.to(device), labels.to(device)
        if training:
            optimizer.zero_grad(set_to_none=True)
        with torch.set_grad_enabled(training):
            with torch.autocast(device_type=device.type, dtype=torch.float16,
                                enabled=device.type == "cuda" and scaler is not None and scaler.is_enabled()):
                logits = model(images)
                loss = criterion(logits, labels)
            if not torch.isfinite(loss):
                raise FloatingPointError("Loss không hữu hạn")
            if training:
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
        denominator = float(criterion.weight[labels].sum()) if criterion.weight is not None else len(labels)
        total_loss += loss.item() * denominator
        total_weight += denominator
        targets.extend(labels.detach().cpu().tolist())
        predictions.extend(logits.detach().argmax(1).cpu().tolist())
    if not targets:
        raise ValueError("Tập dữ liệu rỗng")
    return {"loss": total_loss / total_weight, **metric_summary(targets, predictions)}, targets, predictions


def save_torch(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    torch.save(value, temporary)
    temporary.replace(path)


def configure_stage(model, config, epoch):
    frozen = epoch < config.warmup_epochs
    for parameter in model.features.parameters():
        parameter.requires_grad = not frozen
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],
                                  lr=config.head_lr if frozen else config.finetune_lr,
                                  weight_decay=config.weight_decay)
    return optimizer, frozen


def train(root, records, run_dir, config=None, *, resume=False, provenance=None):
    config = config or TrainConfig()
    if config.epochs < 1 or not 0 <= config.warmup_epochs <= config.epochs or config.patience < 1:
        raise ValueError("Cấu hình epoch/patience không hợp lệ")
    if not config.pretrained and not config.smoke:
        raise ValueError("Thí nghiệm đầy đủ yêu cầu ImageNet pretrained")
    assert_no_leakage(records)
    # Test không bao giờ được đưa vào loader hoặc lựa chọn hyperparameter ở đây.
    train_rows = [r for r in records if r["split"] == "train"]
    val_rows = [r for r in records if r["split"] == "val"]
    for rows in (train_rows, val_rows):
        if {r["label"] for r in rows} != set(range(10)):
            raise ValueError("Train và Validation phải có đủ 10 lớp")
    run_dir = Path(run_dir)
    if run_dir.exists() and any(run_dir.iterdir()) and not resume:
        raise FileExistsError("Run đã tồn tại; chọn tên mới hoặc resume=True")
    run_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    seed_everything(config.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    checkpoint = torch.load(run_dir / "last.pt", map_location="cpu", weights_only=True) if resume else None
    if checkpoint and (checkpoint["config"] != asdict(config) or checkpoint["fingerprint"] != fingerprint):
        raise ValueError("Resume yêu cầu cùng cấu hình và manifest dữ liệu")
    model = build_model(pretrained=config.pretrained and not resume).to(device)
    scaler = torch.amp.GradScaler("cuda", enabled=config.amp and device.type == "cuda")
    counts = Counter(r["label"] for r in train_rows)
    weighted = max(counts.values()) / min(counts.values()) >= config.imbalance_threshold
    weights = torch.tensor([len(train_rows) / (10 * counts[i]) for i in range(10)], device=device)
    train_criterion = torch.nn.CrossEntropyLoss(weight=weights if weighted else None)
    val_criterion = torch.nn.CrossEntropyLoss()  # Loss validation không đổi theo cân bằng Train.
    start, best, stale, history, best_epoch, best_state = 0, -1.0, 0, [], None, None
    if checkpoint:
        model.load_state_dict(checkpoint["model"])
        scaler.load_state_dict(checkpoint["scaler"])
        start, best, stale = checkpoint["epoch"] + 1, checkpoint["best"], checkpoint["stale"]
        history, best_epoch = checkpoint["history"], checkpoint["best_epoch"]
        # last.pt là snapshot nhất quán, kể cả runtime ngắt giữa hai lần ghi file.
        best_state = checkpoint["best_model"]
        save_torch(run_dir / "best.pt", best_state)
    optimizer, frozen = configure_stage(model, config, max(0, start - 1) if resume else 0)
    if checkpoint:
        optimizer.load_state_dict(checkpoint["optimizer"])
    version = checkpoint["model_version"] if checkpoint else run_dir.name
    metadata = {
        "schema_version": 1, "architecture": "mobilenet_v3_large", "num_classes": 10,
        "model_version": version, "source": "trained", "smoke_only": config.smoke,
        "pretrained": "IMAGENET1K_V2" if config.pretrained else None,
        "created_at": checkpoint["created_at"] if checkpoint else datetime.now(timezone.utc).isoformat(),
        "config": asdict(config), "fingerprint": fingerprint, "provenance": provenance or {},
        "train_class_counts": dict(counts), "class_weights": weights.cpu().tolist() if weighted else None,
        "environment": {"python": platform.python_version(), "torch": str(torch.__version__),
                        "torchvision": torchvision.__version__, "cuda": torch.version.cuda,
                        "device": torch.cuda.get_device_name(0) if device.type == "cuda" else "cpu"},
        "limitations": "PlantVillage; chưa đánh giá ESP32-CAM; softmax chưa calibration/OOD.",
    }
    write_json(run_dir / "metadata.json", metadata)
    write_json(run_dir / "class_mapping.json", {name: i for i, name in enumerate(CLASSES)})
    write_json(run_dir / "preprocessing.json", PREPROCESSING)
    write_json(run_dir / "manifest.json", records)
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, check=True)
    (run_dir / "environment.txt").write_text(freeze.stdout, encoding="utf-8")
    for epoch in range(start, config.epochs):
        if stale >= config.patience:
            break
        seed_everything(config.seed + epoch)
        if epoch == config.warmup_epochs:
            optimizer, frozen = configure_stage(model, config, epoch)
        training, _, _ = run_epoch(model, make_loader(root, train_rows, config, True, epoch), device,
                                    train_criterion, scaler, optimizer, frozen)
        validation, _, _ = run_epoch(model, make_loader(root, val_rows, config, False), device,
                                      val_criterion, scaler)
        history.append({"epoch": epoch + 1, "stage": "head" if frozen else "finetune",
                        "train": training, "val": validation})
        score = validation["macro_f1"]
        stale = 0 if score > best + config.min_delta else stale + 1
        if score > best:
            best, best_epoch = score, epoch + 1
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            save_torch(run_dir / "best.pt", best_state)
        save_torch(run_dir / "last.pt", {
            "model": model.state_dict(), "optimizer": optimizer.state_dict(), "scaler": scaler.state_dict(),
            "epoch": epoch, "best": best, "best_epoch": best_epoch, "stale": stale, "history": history,
            "config": asdict(config), "fingerprint": fingerprint, "model_version": version,
            "created_at": metadata["created_at"], "best_model": best_state,
        })
        write_json(run_dir / "history.json", history)
        metadata.update(best_epoch=best_epoch, best_val_macro_f1=best,
                        weights_sha256=hashlib.sha256((run_dir / "best.pt").read_bytes()).hexdigest())
        write_json(run_dir / "metadata.json", metadata)
        print(f"Epoch {epoch + 1}/{config.epochs} ({history[-1]['stage']}): "
              f"loss={training['loss']:.4f}; val_loss={validation['loss']:.4f}; "
              f"val_macro_f1={validation['macro_f1']:.4f}", flush=True)
    metadata.update(best_epoch=best_epoch, best_val_macro_f1=best,
                    weights_sha256=hashlib.sha256((run_dir / "best.pt").read_bytes()).hexdigest())
    write_json(run_dir / "metadata.json", metadata)
    plot_history(history, run_dir)
    return run_dir


def plot_history(history, run_dir):
    figure, axes = plt.subplots(1, 3, figsize=(15, 4))
    for axis, metric in zip(axes, ("loss", "accuracy", "macro_f1")):
        for split in ("train", "val"):
            axis.plot([r["epoch"] for r in history], [r[split][metric] for r in history], label=split)
        axis.set(xlabel="Epoch", title=metric)
        axis.legend()
    figure.tight_layout()
    figure.savefig(Path(run_dir) / "history.png", dpi=150)
    plt.close(figure)


def evaluate(root, records, run_dir, *, split="val", allow_test=False):
    from .inference import Predictor

    run_dir = Path(run_dir)
    if split not in {"val", "test"}:
        raise ValueError("Chỉ đánh giá val/test")
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    if split == "test" and (not allow_test or metadata["smoke_only"]):
        raise ValueError("Test chỉ mở sau khi chốt thí nghiệm đầy đủ; không dùng cho smoke")
    if (run_dir / f"{split}_metrics.json").exists() and split == "test":
        raise FileExistsError("Test đã được đánh giá cho run này; đọc báo cáo đã lưu")
    if records != json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")):
        raise ValueError("Manifest đánh giá phải trùng với manifest của run")
    predictor = Predictor(run_dir)
    rows = [r for r in records if r["split"] == split]
    config = TrainConfig(**metadata["config"])
    metrics, targets, predictions = run_epoch(
        predictor.model, make_loader(root, rows, config, False), predictor.device, torch.nn.CrossEntropyLoss())
    report = classification_report(targets, predictions, labels=list(range(10)), target_names=CLASSES,
                                   output_dict=True, zero_division=0)
    matrix = confusion_matrix(targets, predictions, labels=list(range(10)))
    write_json(run_dir / f"{split}_metrics.json", {
        **metrics, "classification_report": report, "confusion_matrix": matrix.tolist(),
        "model_version": metadata["model_version"], "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "source": "plantvillage", "weights_sha256": metadata["weights_sha256"],
    })
    write_json(run_dir / f"{split}_predictions.json", [
        {"path": row["path"], "target": target, "prediction": prediction}
        for row, target, prediction in zip(rows, targets, predictions)])
    figure, axis = plt.subplots(figsize=(12, 10))
    axis.imshow(matrix, cmap="Blues")
    axis.set(xticks=range(10), yticks=range(10), xticklabels=CLASSES, yticklabels=CLASSES,
             xlabel="Dự đoán", ylabel="Nhãn thật", title=f"Confusion matrix — {split}")
    plt.setp(axis.get_xticklabels(), rotation=90)
    for i in range(10):
        for j in range(10):
            axis.text(j, i, str(matrix[i, j]), ha="center", va="center",
                      color="white" if matrix[i, j] > matrix.max() / 2 else "black")
    figure.tight_layout()
    figure.savefig(run_dir / f"{split}_confusion_matrix.png", dpi=150)
    plt.close(figure)
    return metrics, report
