"""Khôi phục notebook rỗng từ nguồn UTF-8; không thực thi cell hay training."""

import argparse
import ast
import json
from pathlib import Path
from textwrap import dedent


def build_notebook():
    cells = []

    def add(kind, source):
        source = dedent(source).strip() + "\n"
        cell = {"cell_type": kind, "id": f"leaf-{len(cells):02d}",
                "metadata": {}, "source": source.splitlines(keepends=True)}
        if kind == "code":
            ast.parse(source)
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)

    add("markdown", """
# MobileNetV3-Large — bệnh/trạng thái lá cà chua

Huấn luyện bằng hosted GPU runtime **Google Colab**. Không dùng kernel Windows/local.
Dataset giải nén trong `/content`, checkpoint/báo cáo lưu trên Google Drive.
**Run All không tự chạy full training/Test.** Backend tiếp tục dùng Mock AI.
""")
    add("markdown", """
## 1. Chuẩn bị Colab

Đóng gói bằng `scripts/package-colab.ps1` trên Drive (không train).
Upload `aiot-ml-colab.zip` và `plantvillage.zip` vào `MyDrive/aiot-plant-care/`.
Mở notebook trên Colab, chọn runtime Python 3.12 + GPU, chạy từ đầu và cho phép mount Drive.
Nếu bootstrap yêu cầu restart session sau cài dependency, restart rồi chạy lại từ đầu.
""")
    add("code", """
from pathlib import Path
import os
import sys
import hashlib
import zipfile
import importlib

if sys.platform != "linux":
    raise RuntimeError("Chọn hosted runtime Google Colab; không dùng kernel Windows/local.")
try:
    from google.colab import drive
except ImportError as error:
    raise RuntimeError("Notebook yêu cầu hosted runtime Google Colab.") from error
drive.mount("/content/drive")
DRIVE_ROOT = Path(os.getenv("COLAB_DRIVE_ROOT", "/content/drive/MyDrive/aiot-plant-care"))
SOURCE_ZIP = DRIVE_ROOT / "aiot-ml-colab.zip"
if not SOURCE_ZIP.is_file():
    raise FileNotFoundError(f"Upload ZIP mã nguồn trước: {SOURCE_ZIP}")
with SOURCE_ZIP.open("rb") as stream:
    source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
ML_ROOT = Path("/content") / f"aiot-ml-{source_hash[:12]}"
ready = ML_ROOT / ".ready"
if not ready.exists():
    if ML_ROOT.exists() and any(ML_ROOT.iterdir()):
        raise RuntimeError("Source giải nén dở; dùng runtime mới, không trộn source.")
    with zipfile.ZipFile(SOURCE_ZIP) as archive:
        if sum(item.file_size for item in archive.infolist()) > 50 * 1024**2:
            raise ValueError("Source ZIP quá lớn; không đóng kèm dataset/weights.")
        for item in archive.infolist():
            target = (ML_ROOT / item.filename).resolve()
            if (not target.is_relative_to(ML_ROOT) or chr(92) in item.orig_filename
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("Source ZIP có đường dẫn không hợp lệ hoặc symlink")
        archive.extractall(ML_ROOT)
    ready.write_text(source_hash, encoding="utf-8")
sys.path.insert(0, str(ML_ROOT))
if "colab_runtime" in sys.modules and Path(sys.modules["colab_runtime"].__file__).resolve() != ML_ROOT / "colab_runtime.py":
    raise RuntimeError("Source ZIP changed: restart session before continuing.")
colab_runtime = importlib.import_module("colab_runtime")
prepare_runtime = colab_runtime.prepare_runtime
extract_zip_once = colab_runtime.extract_zip_once

INSTALL_DEPS = True  # Nếu thư viện đã đúng lock, không cài lại.
RUNTIME_INFO = prepare_runtime(ML_ROOT, require_gpu=True, install=INSTALL_DEPS)
CODE_ROOT = ML_ROOT / "leaf-disease"
sys.path.insert(0, str(CODE_ROOT))
os.chdir(CODE_ROOT)
print("Kernel Colab:", sys.executable, "| Source SHA256:", source_hash)
""")
    add("markdown", """
## 2. Xác nhận GPU Colab

Kiểm tra GPU thực tế được cấp; không giả định luôn có T4. Giữ Torch/CUDA của runtime.
Batch 32, workers 2 là cấu hình khởi đầu; giảm batch khi OOM và tạo run mới.
Checkpoint lưu trong `MyDrive/aiot-plant-care/leaf-runs/`.
""")
    add("code", """
import importlib.metadata
import json
import platform
import numpy as np
import PIL
import sklearn
import torch
import torchvision
from packaging.version import Version
from datetime import datetime, timezone
from IPython.display import display, Image as DisplayImage
from leaf_training.data import (CLASSES, discover_classes, audit_dataset, split_records,
                                subset_for_smoke, write_json)
from leaf_training.train import TrainConfig, train, evaluate
from leaf_training.inference import Predictor

for package, loaded in [("numpy", np.__version__), ("Pillow", PIL.__version__),
                        ("scikit-learn", sklearn.__version__)]:
    if loaded != importlib.metadata.version(package):
        raise RuntimeError(f"Restart kernel: {package} đang giữ phiên bản cũ trong bộ nhớ")
if Version(torch.__version__.split("+")[0]) < Version("2.6"):
    raise RuntimeError("Cần torch >=2.6; chọn runtime Colab tương thích.")
print("Python:", platform.python_version(), "torch:", torch.__version__,
      "torchvision:", torchvision.__version__, "CUDA:", torch.version.cuda)
print("CUDA available:", torch.cuda.is_available())
if not torch.cuda.is_available():
    raise RuntimeError("CUDA không khả dụng; chọn GPU runtime Colab.")
print("GPU:", torch.cuda.get_device_name(0))
print("VRAM GB:", round(torch.cuda.get_device_properties(0).total_memory / 2**30, 2))

RUNS_ROOT = Path(os.getenv("LEAF_OUTPUT_ROOT", str(DRIVE_ROOT / "leaf-runs")))
RUNS_ROOT.mkdir(parents=True, exist_ok=True)
SESSION_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
AUDIT_DIR = RUNS_ROOT / f"audit-{SESSION_ID}"
FULL_RUN = None
print("Checkpoint và báo cáo:", RUNS_ROOT)
""")
    add("markdown", """
## 3. Dữ liệu trên Colab

Mặc định giải nén `plantvillage.zip` từ Drive sang ổ đĩa runtime để giảm I/O Drive.
Có thể chọn `DATA_SOURCE="kaggle"`; nếu cần đăng nhập, dùng `kagglehub.login()` riêng,
không ghi token vào notebook. Ghim handle `/versions/N` sau lần tải đầu để tái lập.
""")
    add("code", """
import kagglehub

DATA_SOURCE = "drive_zip"
KAGGLE_HANDLE = "charuchaudhry/plantvillage-tomato-leaf-dataset"
if DATA_SOURCE == "drive_zip":
    DATA_ROOT = extract_zip_once(DRIVE_ROOT / "plantvillage.zip", Path("/content/aiot-data/plantvillage"))
elif DATA_SOURCE == "kaggle":
    DATA_ROOT = Path(kagglehub.dataset_download(KAGGLE_HANDLE))
else:
    raise ValueError("DATA_SOURCE phải là drive_zip hoặc kaggle")
files, folders = discover_classes(DATA_ROOT)
for folder in folders:
    print(folder)
print("Root:", DATA_ROOT, "| Số ảnh:", len(files))
PROVENANCE = {
    "dataset_url": "https://www.kaggle.com/datasets/charuchaudhry/plantvillage-tomato-leaf-dataset",
    "handle": KAGGLE_HANDLE if DATA_SOURCE == "kaggle" else None,
    "source": DATA_SOURCE, "resolved_root": str(DATA_ROOT), "code_sha256": source_hash,
    "observed_at": datetime.now(timezone.utc).isoformat(),
}
write_json(AUDIT_DIR / "source.json", PROVENANCE)
""")
    add("markdown", """
## 4. Kiểm tra ảnh lỗi, trùng và gần trùng

Audit đọc toàn bộ ảnh trước chia tập, không xóa ảnh gốc. Nhóm mâu thuẫn nhãn được cách ly.
Nếu có metadata cùng lá, cung cấp CSV `path,leaf_id` với path tương đối DATA_ROOT, dùng `/`.
pHash không bảo đảm phát hiện cùng lá khác góc/ánh sáng. Xem báo cáo và các cặp ảnh bằng mắt;
chưa có leaf ID thì chưa thể khẳng định độc lập theo chiếc lá.
""")
    add("code", """
import matplotlib.pyplot as plt
from leaf_training.model import open_rgb

LEAF_GROUPS_CSV = None
PHASH_DISTANCE = 4
clean_records, audit = audit_dataset(DATA_ROOT, AUDIT_DIR, distance=PHASH_DISTANCE,
                                     leaf_groups_csv=LEAF_GROUPS_CSV)
print({key: audit[key] for key in ("raw_count", "valid_count", "clean_count", "leaf_metadata_count")})
print("Ảnh lỗi:", len(audit["corrupt"]), "| Ảnh loại:", len(audit["excluded"]),
      "| Nhóm trùng/gần trùng:", len(audit["groups"]))
print("Báo cáo đầy đủ:", AUDIT_DIR / "audit.json")

fig, ax = plt.subplots(figsize=(10, 5))
ax.barh(CLASSES, [audit["class_counts"].get(name, 0) for name in CLASSES])
ax.set(xlabel="Số ảnh còn lại", title="Phân bố lớp sau audit")
plt.tight_layout()
plt.show()

pairs = audit["near_pairs"][:8]  # Mẫu hiển thị; đọc audit.json để rà soát đầy đủ.
if pairs:
    fig, axes = plt.subplots(len(pairs), 2, figsize=(9, 3 * len(pairs)), squeeze=False)
    for axes_row, pair in zip(axes, pairs):
        for ax, key in zip(axes_row, ("a", "b")):
            ax.imshow(open_rgb(DATA_ROOT / pair[key]))
            ax.set_title(f"{Path(pair[key]).parent.name} | d={pair['distance']}", fontsize=8)
            ax.axis("off")
    plt.tight_layout()
    plt.show()
""")
    add("markdown", """
## 5. Chia Train / Validation / Test

Seed 42, tỷ lệ 70/15/15 theo nhóm từng lớp; số ảnh có thể lệch do kích thước nhóm khác nhau.
Manifest và fingerprint lưu trong Google Drive. Ảnh cùng nhóm/hash/leaf_id không được ở khác tập.
Augmentation chỉ dùng Train. Trọng số cân bằng lớp chỉ tính từ Train.
""")
    add("code", """
from collections import Counter
records, split_fingerprint = split_records(clean_records, AUDIT_DIR, seed=42)
for split in ("train", "val", "test"):
    rows = [r for r in records if r["split"] == split]
    print(split, "ảnh:", len(rows), "nhóm:", len({r["group_id"] for r in rows}),
          "phân bố:", dict(Counter(r["class_name"] for r in rows)))
print("Fingerprint:", split_fingerprint)
""")
    add("markdown", """
## 6. Smoke 1 epoch — ImageNet pretrained, tối đa 80 Train + 80 Validation

Kiểm tra training classifier, metrics Validation, checkpoint, load và inference.
Sau đó thử một batch backprop qua backbone fine-tuning. Không đọc Test.
Smoke metrics không phải chất lượng mô hình cuối. Nếu OOM, giảm batch và dùng tên run mới.
""")
    add("code", """
from leaf_training.train import configure_stage, make_loader, run_epoch

SMOKE_OK = False
smoke_records = subset_for_smoke(records, per_class=8, seed=42)
smoke_config = TrainConfig(epochs=1, warmup_epochs=1, batch_size=16, workers=0, smoke=True)
SMOKE_RUN = RUNS_ROOT / f"smoke-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')}"
train(DATA_ROOT, smoke_records, SMOKE_RUN, smoke_config, provenance=PROVENANCE)
smoke_metrics, smoke_report = evaluate(DATA_ROOT, smoke_records, SMOKE_RUN, split="val")
print(json.dumps(smoke_metrics, indent=2))
print(json.dumps(smoke_report, indent=2))
predictor = Predictor(SMOKE_RUN)
print(predictor.predict(DATA_ROOT / smoke_records[0]["path"], source="plantvillage_smoke"))
display(DisplayImage(filename=str(SMOKE_RUN / "history.png")))
display(DisplayImage(filename=str(SMOKE_RUN / "val_confusion_matrix.png")))

probe_config = TrainConfig(epochs=1, warmup_epochs=0, batch_size=10, workers=0, smoke=True)
optimizer, frozen = configure_stage(predictor.model, probe_config, 0)
scaler = torch.amp.GradScaler("cuda", enabled=True)
probe_rows = [next(r for r in smoke_records if r["split"] == "train" and r["label"] == label)
              for label in range(10)]
probe_metrics, _, _ = run_epoch(predictor.model,
    make_loader(DATA_ROOT, probe_rows, probe_config, True), predictor.device,
    torch.nn.CrossEntropyLoss(), scaler, optimizer, frozen)
assert any(p.grad is not None and torch.isfinite(p.grad).all() and p.grad.abs().sum() > 0
           for p in predictor.model.features.parameters())
SMOKE_OK = True
write_json(SMOKE_RUN / "smoke_success.json", {
    "fine_tune_probe": probe_metrics, "split_fingerprint": split_fingerprint,
    "passed_at": datetime.now(timezone.utc).isoformat(),
})
del predictor, optimizer, scaler
torch.cuda.empty_cache()
print("Smoke thành công. Hãy rà soát dữ liệu trước khi bật full training.")
""")
    add("markdown", """
## 7. Huấn luyện đầy đủ — mặc định tắt

Chỉ bật hai cờ sau smoke thành công và rà soát audit/phân bố/giới hạn leaf ID.
Khởi tạo lại ImageNet pretrained; không dùng weights smoke. Đóng băng backbone 2 epoch,
sau đó fine-tune, tối đa 20 epoch. Best theo Validation Macro F1, early stopping patience 5.
`last.pt` và `best.pt` ghi vào Google Drive mỗi epoch; giữ nguyên cả thư mục run để resume.
""")
    add("code", """
RUN_FULL_TRAINING = False
DATA_REVIEWED = False
full_config = TrainConfig(epochs=20, warmup_epochs=2, batch_size=32, workers=2)
if RUN_FULL_TRAINING:
    if not SMOKE_OK or not DATA_REVIEWED:
        raise RuntimeError("Cần smoke thành công và xác nhận đã rà soát dữ liệu.")
    FULL_RUN = RUNS_ROOT / f"mobilenetv3-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')}"
    train(DATA_ROOT, records, FULL_RUN, full_config, provenance=PROVENANCE)
    validation_metrics, validation_report = evaluate(DATA_ROOT, records, FULL_RUN, split="val")
    print(json.dumps(validation_metrics, indent=2))
    print(json.dumps(validation_report, indent=2))
    display(DisplayImage(filename=str(FULL_RUN / "history.png")))
else:
    print("Full training đang tắt.")
""")
    add("markdown", """
## 8. Resume sau khi runtime ngắt (tùy chọn)

Chạy lại bước 1–3, tải đúng dataset version, rồi chạy cell này với tên run cũ.
Dùng manifest đã lưu và kiểm tra SHA256 từng ảnh. Không cần chạy lại smoke nếu tiếp tục
run đã xác nhận. Resume từ epoch hoàn tất gần nhất, epoch đang dở chạy lại từ đầu.
Kiểm tra checkpoint đã ghi xong trước khi chủ động ngắt runtime; Drive có thể chưa đồng bộ nếu mất kết nối.
""")
    add("code", """
RESUME_TRAINING = False
RESUME_RUN = RUNS_ROOT / "dien-ten-run-cu"
if RESUME_TRAINING:
    import hashlib
    resumed_metadata = json.loads((RESUME_RUN / "metadata.json").read_text(encoding="utf-8"))
    records = json.loads((RESUME_RUN / "manifest.json").read_text(encoding="utf-8"))
    for row in records:
        if hashlib.sha256((DATA_ROOT / row["path"]).read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"Dataset đã thay đổi: {row['path']}")
    full_config = TrainConfig(**resumed_metadata["config"])
    train(DATA_ROOT, records, RESUME_RUN, full_config, resume=True,
          provenance=resumed_metadata["provenance"])
    FULL_RUN = RESUME_RUN
    print(evaluate(DATA_ROOT, records, FULL_RUN, split="val")[0])
""")
    add("markdown", """
## 9. Đánh giá Test một lần sau khi chốt cấu hình

Không dùng Test điều chỉnh hyperparameter hoặc chọn model. Run đã có test_metrics.json
sẽ từ chối đánh giá lại; đọc báo cáo cũ. PlantVillage chưa đại diện ảnh ESP32-CAM thực tế.
""")
    add("code", """
FINAL_EVALUATION = False
if FINAL_EVALUATION:
    if FULL_RUN is None:
        raise RuntimeError("Chưa có run đầy đủ; cần huấn luyện hoặc resume trước.")
    test_metrics, test_report = evaluate(DATA_ROOT, records, FULL_RUN, split="test", allow_test=True)
    print(json.dumps(test_metrics, indent=2))
    print(json.dumps(test_report, indent=2))
    display(DisplayImage(filename=str(FULL_RUN / "test_confusion_matrix.png")))
else:
    print("Test đang khóa; chỉ dùng Validation để chọn cấu hình.")
""")
    add("markdown", """
## 10. Inference trên ảnh mới và lấy artifact

Đặt ảnh trên Drive và điền NEW_IMAGE. Dùng run đầy đủ cho đánh giá thực tế;
smoke chỉ kiểm tra thao tác. Softmax chưa calibration, chưa có phát hiện ảnh ngoài phạm vi.
Artifact đã ở Google Drive: best.pt, class_mapping.json, preprocessing.json, metadata.json,
manifest.json, environment.txt, history và các báo cáo. Giữ last.pt để resume, không commit Git.
""")
    add("code", """
NEW_IMAGE = None  # Ví dụ DRIVE_ROOT / "new-leaf.jpg"
if NEW_IMAGE is not None:
    ARTIFACT_DIR = FULL_RUN if FULL_RUN is not None else SMOKE_RUN
    predictor = Predictor(ARTIFACT_DIR)
    print(predictor.predict(NEW_IMAGE, source="manual"))
    display(DisplayImage(filename=str(NEW_IMAGE)))
""")
    return {"cells": cells, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python"}, "accelerator": "GPU", "colab": {"provenance": []},
    }, "nbformat": 4, "nbformat_minor": 5}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Ghi đè notebook đã có cell")
    args = parser.parse_args()
    destination = Path(__file__).resolve().parents[1] / "notebooks" / "train_mobilenetv3.ipynb"
    if destination.exists() and not args.force:
        old = json.loads(destination.read_text(encoding="utf-8"))
        if old.get("cells"):
            raise SystemExit("Notebook đã có nội dung. Chỉ dùng --force khi chủ động muốn tạo lại.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    notebook = build_notebook()
    temporary = destination.with_suffix(".ipynb.tmp")
    temporary.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    temporary.replace(destination)
    print(f"{destination}: {len(notebook['cells'])} cells")
