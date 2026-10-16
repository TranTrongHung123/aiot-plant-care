"""Tạo notebook thí nghiệm có thể chạy bằng hosted runtime Google Colab."""

import argparse
import json
from pathlib import Path
from textwrap import dedent

cells = []


def cell(kind, source):
    item = {"cell_type": kind, "metadata": {}, "id": f"fruit-{len(cells):02d}",
            "source": dedent(source).strip() + "\n"}
    if kind == "code":
        item.update(execution_count=None, outputs=[])
    cells.append(item)


cell("markdown", """
# YOLO11n — phát hiện và phân loại độ chín quả

Hai lớp ripe/unripe, chưa nhận diện bệnh quả. Huấn luyện bằng hosted GPU runtime Colab.
Đóng gói bằng `scripts/package-colab.ps1`, upload source ZIP và `tomato-ripeness-v4.zip`
vào `MyDrive/aiot-plant-care/`. Chọn runtime Python 3.12 + GPU; cho phép mount Drive.
Train đầy đủ/Test mặc định tắt. Smoke chỉ kiểm tra pipeline, không chứng minh chất lượng.
""")
cell("code", """
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
CODE_ROOT = ML_ROOT / "fruit-health"
sys.path.insert(0, str(CODE_ROOT))
os.chdir(CODE_ROOT)
print("Kernel Colab:", sys.executable, "| Source SHA256:", source_hash)

RUNS_ROOT = Path(os.getenv("FRUIT_OUTPUT_ROOT", str(DRIVE_ROOT / "fruit-runs")))
RUNS_ROOT.mkdir(parents=True, exist_ok=True)
""")
cell("markdown", """
## Môi trường Colab

Bootstrap cài lock bổ sung bằng `--no-deps`, giữ Torch/CUDA của runtime.
Nếu yêu cầu restart session, restart rồi chạy lại từ đầu. Không cài lock Windows.
Batch 16, workers 2 là cấu hình khởi đầu, giảm batch khi OOM.
""")
cell("code", """
import importlib.metadata as metadata
import torch
import torchvision

if not torch.cuda.is_available():
    raise RuntimeError("Chọn GPU runtime Colab rồi chạy lại bootstrap")
if metadata.version("ultralytics") != "8.3.203":
    raise RuntimeError("Cần Ultralytics đúng requirements-colab.lock")
print({"torch": torch.__version__, "torchvision": torchvision.__version__,
       "cuda": torch.version.cuda, "gpu": torch.cuda.get_device_name(0),
       "vram_gib": round(torch.cuda.get_device_properties(0).total_memory / 2**30, 2)})
torch.set_num_threads(2)
""")
cell("markdown", """
## Dataset v4

[Tomato Ripeness v4](https://universe.roboflow.com/uni-zkvyc/tomato-ripeness-um0yl/dataset/4), CC BY 4.0.
Mặc định giải nén ZIP từ Drive vào `/content`; raw polygon được chuyển sang bbox trong bản riêng.
Tùy chọn `roboflow` lấy key từ Colab Secrets `ROBOFLOW_API_KEY`, không ghi key vào notebook.
""")
cell("code", """
from fruit_training.download import download_roboflow

DATA_SOURCE = "drive_zip"
RAW_DEST = Path("/content/aiot-data/tomato-ripeness-v4")
if DATA_SOURCE == "drive_zip":
    extracted = extract_zip_once(DRIVE_ROOT / "tomato-ripeness-v4.zip", RAW_DEST)
    candidates = list(extracted.rglob("data.yaml"))
    if len(candidates) != 1:
        raise ValueError("ZIP cần đúng một data.yaml")
    DATA_ROOT = candidates[0].parent
elif DATA_SOURCE == "roboflow":
    from google.colab import userdata
    existing = list(RAW_DEST.rglob("data.yaml")) if RAW_DEST.exists() else []
    if len(existing) == 1:
        DATA_ROOT = existing[0].parent  # Audit sẽ kiểm tra dữ liệu trước khi train.
    else:
        os.environ["ROBOFLOW_API_KEY"] = userdata.get("ROBOFLOW_API_KEY")
        DATA_ROOT = download_roboflow(RAW_DEST)
else:
    raise ValueError("DATA_SOURCE phải là drive_zip hoặc roboflow")
print("Dataset:", DATA_ROOT)
""")
cell("markdown", """
## Audit nhãn, ảnh trùng và rò rỉ giữa các tập
Giữ split nguồn; nhóm ảnh theo SHA/pixel, tên ảnh trước `.rf.`, pHash và CSV group tùy chọn.
Nhóm giao nhiều tập được giữ phía **Test > Validation > Train**, loại phía còn lại.
Ảnh trùng pixel nhưng nhãn khác nhau được cách ly. Không đưa ảnh Test trở lại Train.
pHash/tên ảnh là heuristic, có thể gom nhầm; cũng không đảm bảo phát hiện mọi ảnh cùng cây/video.
Rà soát nhóm lớn, ảnh gần trùng và metadata cảnh trước thí nghiệm đầy đủ.
`groups.csv` nếu có: hai cột `path,group_id`; path tương đối từ DATA_ROOT, dấu `/`.
Audit có thể mất vài phút; không dùng kết quả Test để điều chỉnh rule này.
""")
cell("code", """
import json
from datetime import datetime, timezone
import pandas as pd
from fruit_training.data import (audit_dataset, prepare_dataset, smoke_subset,
                                 verify_prepared, manifest_fingerprint)

SESSION = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
SEED = int(os.getenv("FRUIT_SEED", "42"))
GROUPS_CSV = None  # ví dụ DATA_ROOT / "groups.csv", do bạn xác nhận cảnh/cây/video
records, audit = audit_dataset(DATA_ROOT, RUNS_ROOT / f"audit-{SESSION}", groups_csv=GROUPS_CSV)
display(pd.DataFrame({"raw_images": audit["inventory"], "clean_images": audit["clean_split_images"],
                      "negative_images": audit["negative_images"]}))
display(pd.DataFrame(audit["class_box_counts"]).T)
print("Ảnh hợp lệ:", audit["valid_images"], "Polygon:", audit["raw_polygon_count"])
print("Loại/cách ly:", pd.Series([row["reason"] for row in audit["excluded"]]).value_counts().to_dict())
print("Nhóm lớn nhất:", max((len(g["paths"]) for g in audit["groups"]), default=1))
display(pd.DataFrame(sorted(audit["groups"], key=lambda g: -len(g["paths"]))[:10]))
""")
cell("code", """
import matplotlib.pyplot as plt
from PIL import Image
from matplotlib.patches import Rectangle

pd.DataFrame(audit["class_box_counts"]).T.plot.bar(title="Số bounding box theo lớp / split")
plt.ylabel("Số quả được gán nhãn")
plt.tight_layout()
plt.show()
examples = [row for row in records if row["split"] == "train"][:4]
fig, axes = plt.subplots(1, len(examples), figsize=(16, 4), squeeze=False)
for ax, row in zip(axes.ravel(), examples):
    with Image.open(DATA_ROOT / row["path"]) as original:
        img = original.convert("RGB")
    ax.imshow(img)
    for label, x, y, w, h in row["boxes"]:
        left, top = (x - w/2) * img.width, (y - h/2) * img.height
        color = "red" if label == 0 else "lime"
        ax.add_patch(Rectangle((left, top), w * img.width, h * img.height,
                               fill=False, edgecolor=color, linewidth=2))
        ax.text(left, top, audit["names"][label], color=color)
    ax.axis("off")
plt.show()
""")
cell("markdown", """
## Smoke test: 1 epoch, 16 Train + 16 Validation, không có Test
Tự chọn CUDA và AMP trên GPU; CPU vẫn chạy được. Lần đầu tải YOLO11n pretrained COCO.
Smoke lưu vào thư mục riêng và gắn `smoke_only=true`; tuyệt đối không dùng cho vận hành.
""")
cell("code", """
from fruit_training.train import TrainConfig, train, evaluate
from fruit_training.inference import FruitPredictor

smoke_rows = smoke_subset(records, per_split=16, seed=SEED)
smoke_yaml = prepare_dataset(DATA_ROOT, smoke_rows, CODE_ROOT / "data" / f"smoke-{SESSION}")
SMOKE_RUN = train(smoke_yaml, RUNS_ROOT / f"smoke-{SESSION}",
                  config=TrainConfig(epochs=1, imgsz=320, batch=4, workers=0, seed=SEED, smoke=True))
smoke_metrics = evaluate(smoke_yaml, SMOKE_RUN)
display(smoke_metrics)
smoke_example = next(row for row in smoke_rows if row["split"] == "val")
display(FruitPredictor(SMOKE_RUN).predict(DATA_ROOT / smoke_example["path"], source="smoke",
                                         annotated_path=SMOKE_RUN / "example.jpg"))
SMOKE_PASSED = True
""")
cell("markdown", """
## Huấn luyện đầy đủ — chỉ bật sau khi smoke thành công và đã rà soát dữ liệu
Fine-tune toàn bộ YOLO11n 80 epoch tối đa, early stopping 15 epoch, best theo Validation mAP50–95.
Augmentation chỉ Train; hue bằng 0 để hạn chế làm đổi màu độ chín. Không oversample box riêng lẻ.
Ngưỡng đếm mặc định 0,25/NMS IoU 0,7 được lưu cùng model; điều chỉnh chỉ trên Validation trong run mới.
Loss, PR/F1, confusion matrix và đường mAP được Ultralytics xuất trong `yolo/`.
Checkpoint `yolo/weights/last.pt` và `best.pt` ghi mỗi epoch vào RUNS_ROOT trên Drive.

Resume run **bị ngắt giữa chừng**: đặt FULL_RUN về thư mục Drive cũ, RESUME=True và RUN_FULL=True.
Giữ cùng dataset, đường dẫn prepared, config/seed. Sau runtime reset chạy lại bootstrap + audit;
prepared-full được tạo lại ở cùng đường dẫn. Có thể bỏ qua cell smoke vì run cũ đã qua smoke.
Checkpoint của run đã kết thúc bị Ultralytics bỏ optimizer; không resume run hoàn tất.
Ổ đĩa cần đủ dung lượng; epoch đang ghi có thể mất nếu runtime ngắt đột ngột.
""")
cell("code", """
RUN_FULL = False
DATA_REVIEWED = False
RESUME = False
FULL_RUN = RUNS_ROOT / f"full-{SESSION}"  # khi resume: thay bằng thư mục run cũ trên Drive
FULL_CONFIG = TrainConfig(epochs=80, imgsz=640, batch=16, workers=2, seed=SEED)
FULL_YAML = Path("/content/aiot-data/fruit-prepared-full/data.yaml")

if RUN_FULL:
    if not DATA_REVIEWED or (not RESUME and not globals().get("SMOKE_PASSED", False)):
        raise RuntimeError("Cần smoke thành công và xác nhận đã rà soát audit")
    if FULL_YAML.exists():
        if verify_prepared(FULL_YAML)["fingerprint"] != manifest_fingerprint(records):
            raise ValueError("Prepared cũ khác audit: chọn đường dẫn prepared mới, không ghi đè")
    else:
        prepare_dataset(DATA_ROOT, records, FULL_YAML.parent)
    if RESUME:
        old = json.loads((FULL_RUN / "run_config.json").read_text(encoding="utf-8"))
        FULL_CONFIG = TrainConfig(**old["config"])
    FULL_RUN = train(FULL_YAML, FULL_RUN, config=FULL_CONFIG, resume=RESUME)
    display(evaluate(FULL_YAML, FULL_RUN))
else:
    print("Chưa chạy training đầy đủ. Rà soát audit rồi chủ động bật RUN_FULL và DATA_REVIEWED.")
""")
cell("code", """
from IPython.display import display, Image as DisplayImage

ARTIFACT = FULL_RUN if RUN_FULL and (FULL_RUN / "metadata.json").exists() else globals().get("SMOKE_RUN")
if ARTIFACT is not None:
    history = pd.read_csv(ARTIFACT / "yolo" / "results.csv")
    history.columns = history.columns.str.strip()
    loss_columns = [name for name in history if name.endswith("_loss")]
    history.plot(x="epoch", y=loss_columns, marker=".", title="Training / Validation loss")
    plt.show()
    metric_columns = [name for name in history if "metrics/" in name]
    history.plot(x="epoch", y=metric_columns, marker=".", title="Validation metrics")
    plt.show()
    for name in ("confusion_matrix_normalized.png", "BoxPR_curve.png"):
        path = ARTIFACT / "evaluation" / "val" / name
        if path.exists():
            display(DisplayImage(filename=str(path), width=700))
""")
cell("markdown", """
## Test cuối cùng
Chỉ mở sau khi chốt kiến trúc, epoch, preprocessing và ngưỡng bằng Validation.
Không huấn luyện/tối ưu theo Test. Bộ test Roboflow vẫn có khác biệt so với ảnh ESP32-CAM;
cần tập camera thật riêng theo cảnh/cây để kết luận khả năng triển khai.
Mỗi artifact chỉ ghi một báo cáo Test; cell chạy lại sẽ đọc báo cáo sẵn có.
""")
cell("code", """
RUN_FINAL_TEST = False
if RUN_FINAL_TEST:
    if not (FULL_RUN / "metadata.json").exists():
        raise RuntimeError("Cần model đầy đủ đã hoàn tất; không dùng smoke cho Test")
    report_path = FULL_RUN / "test_metrics.json"
    if report_path.exists():
        display(json.loads(report_path.read_text(encoding="utf-8")))
    else:
        display(evaluate(FULL_YAML, FULL_RUN, split="test", allow_test=True))
else:
    print("Test đang khóa.")
""")
cell("markdown", """
## Inference ảnh mới và bàn giao artifact
Đặt NEW_IMAGE tới ảnh trong Google Drive. Output gồm bbox pixel + chuẩn hóa, confidence,
ripe/unripe, số quả từng lớp, version và thời điểm UTC. Đây là đếm từng ảnh, không tracking.
Artifact cần chuyển cùng nhau: `best.pt`, `metadata.json`, `class_mapping.json`, `preprocessing.json`.
Chỉ load checkpoint tin cậy. Chưa nối vào backend Mock AI và chưa điều khiển bơm.
""")
cell("code", """
NEW_IMAGE = None  # ví dụ DRIVE_ROOT / "tomato-camera.jpg"
if NEW_IMAGE is not None:
    if ARTIFACT is None:
        raise RuntimeError("Chưa có artifact; train hoặc chỉ định thư mục artifact đã lưu")
    predictor = FruitPredictor(ARTIFACT)
    annotated = ARTIFACT / f"inference-{SESSION}.jpg"
    display(predictor.predict(NEW_IMAGE, source="manual", annotated_path=annotated))
    display(DisplayImage(filename=str(annotated), width=700))
""")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Ghi đè notebook đã có cell")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / "notebooks" / "train_yolo_fruit.ipynb"
    if target.exists() and not args.force:
        if json.loads(target.read_text(encoding="utf-8")).get("cells"):
            raise SystemExit("Notebook đã có nội dung; chỉ dùng --force khi chủ động tạo lại.")
    target.parent.mkdir(parents=True, exist_ok=True)
    notebook = {"cells": cells, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12"},
        "accelerator": "GPU", "colab": {"provenance": []}}, "nbformat": 4, "nbformat_minor": 5}
    target.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Created {target} ({len(cells)} cells)")


if __name__ == "__main__":
    main()
