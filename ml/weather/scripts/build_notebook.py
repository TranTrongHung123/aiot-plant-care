"""Tạo notebook XGBoost có nội dung; không chạy training và không ghi đè mặc định."""

import argparse
import ast
import json
from pathlib import Path
from textwrap import dedent


def build_notebook():
    cells = []

    def add(kind, source):
        source = dedent(source).strip() + "\n"
        cell = {"cell_type": kind, "metadata": {}, "id": f"weather-{len(cells):02d}",
                "source": source.splitlines(keepends=True)}
        if kind == "code":
            ast.parse(source)
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)

    add("markdown", """
# XGBoost — dự báo giáng thủy trong 60 phút tới

Mô hình thứ hai của AIoT Plant Care. Chạy CPU Colab là đủ; không cần dùng GPU.
CSV hiện có chứa `precipitation` (mưa + tuyết), nên đây là **nhãn đại diện cho mưa**.
Nhãn tại `t`: tổng giáng thủy `(t,t+60 phút] >= 0.1 mm`, lấy từ dòng `t+1h`.
Chỉ dùng đặc trưng tại hoặc trước `t`. Không dùng độ ẩm đất để đoán thay khí tượng.
**Run All chỉ chạy audit và smoke; full training và Test mặc định tắt.**
""")
    add("markdown", """
## 1. Chuẩn bị Colab CPU

Đóng gói bằng `scripts/package-colab.ps1`. Upload source ZIP và `open-meteo.csv`
vào `MyDrive/aiot-plant-care/`. Mở notebook trên hosted Colab Python 3.12,
chọn CPU runtime; không cần dành GPU cho XGBoost.
Bootstrap giữ Torch của runtime, cài dependency bổ sung; restart session nếu được yêu cầu.
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
RUNTIME_INFO = prepare_runtime(ML_ROOT, require_gpu=False, install=INSTALL_DEPS)
CODE_ROOT = ML_ROOT / "weather"
sys.path.insert(0, str(CODE_ROOT))
os.chdir(CODE_ROOT)
print("Kernel Colab:", sys.executable, "| Source SHA256:", source_hash)
""")
    add("markdown", """
## 2. Đọc CSV sẵn có hoặc tải Open-Meteo trực tiếp

Mặc định đọc CSV của dự án. `DATA_SOURCE="download"` tải từ Open-Meteo theo từng năm.
Tọa độ mặc định 20.96/105.76 lấy từ URL nguồn trong `ml/README.md`, chưa xác nhận là vị trí cây thật.
CSV có metadata Asia/Bangkok UTC+7; loader đổi sang UTC và gió km/h → m/s.
Dữ liệu lịch sử tái dựng không chứng minh giá trị đã sẵn có đúng thời điểm trong vận hành.
""")
    add("code", """
import json
import pandas as pd
import numpy as np
import xgboost as xgb
import importlib.metadata
from datetime import datetime, timezone
from IPython.display import display, Image as DisplayImage
from weather_training.data import load_open_meteo_csv, download_open_meteo, write_json
from weather_training.features import FeatureConfig, make_supervised, chronological_split
from weather_training.train import TrainConfig, train, evaluate_test
from weather_training.inference import RainPredictor

for package, loaded in [("numpy", np.__version__), ("pandas", pd.__version__), ("xgboost", xgb.__version__)]:
    if loaded != importlib.metadata.version(package):
        raise RuntimeError(f"Restart kernel: {package} giữ phiên bản cũ trong bộ nhớ")
print("XGBoost:", xgb.__version__, "| device: CPU")
DATA_SOURCE = "csv"
DATA_PATH = Path(os.getenv("WEATHER_DATA_PATH", str(DRIVE_ROOT / "open-meteo.csv")))
OUTPUT_ROOT = Path(os.getenv("WEATHER_OUTPUT_ROOT", str(DRIVE_ROOT / "weather-runs")))
SESSION_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
AUDIT_DIR = OUTPUT_ROOT / f"audit-{SESSION_ID}"
if DATA_SOURCE == "csv":
    hourly, source = load_open_meteo_csv(DATA_PATH)
elif DATA_SOURCE == "download":
    hourly, source = download_open_meteo(CODE_ROOT / "data" / f"download-{SESSION_ID}",
        latitude=float(os.getenv("WEATHER_LATITUDE", "20.96")),
        longitude=float(os.getenv("WEATHER_LONGITUDE", "105.76")),
        start_date=os.getenv("WEATHER_START_DATE", "2021-01-01"),
        end_date=os.getenv("WEATHER_END_DATE", "2026-10-10"),
        model=os.getenv("WEATHER_ARCHIVE_MODEL", "ecmwf_ifs"))
else:
    raise ValueError("DATA_SOURCE phải là csv hoặc download")
feature_config = FeatureConfig(rain_threshold_mm=float(os.getenv("WEATHER_RAIN_THRESHOLD_MM", "0.1")))
table, feature_report = make_supervised(hourly, feature_config)
feature_names = feature_report["feature_names"]
write_json(AUDIT_DIR / "source.json", source)
write_json(AUDIT_DIR / "features.json", feature_report)
print(json.dumps(source["audit"], indent=2))
print("Giờ đủ điều kiện:", len(table), "| Đặc trưng:", len(feature_names))
display(hourly.head())
""")
    add("markdown", """
## 3. Kiểm tra dữ liệu và phân chia thời gian

Không shuffle hoặc nội suy ngược. Thiếu giờ/giá trị sẽ loại cửa sổ liên quan;
timestamp trùng mâu thuẫn bị từ chối. Chia 60/10/10/10/10, embargo 6 giờ ở ranh giới:
Train → Validation dừng sớm → Calibration → Validation chọn threshold → Test.
Sigmoid calibration và threshold không học trên cùng khoảng thời gian.
Test chỉ hiển thị phạm vi/số dòng ở đây, không đánh giá chất lượng model.
""")
    add("code", """
import matplotlib.pyplot as plt
splits = chronological_split(table)
for name, block in splits.items():
    print(name, len(block), block.index.min(), "→", block.index.max())
train_block = splits["train"]
monthly = train_block.target.resample("MS").mean()
ax = monthly.plot(figsize=(11, 3), title="Tỷ lệ giáng thủy theo tháng — chỉ Train")
ax.set_ylabel("Tỷ lệ nhãn dương")
plt.show()
print("Phân bố Train:", train_block.target.value_counts().to_dict())
print("Đặc trưng:", feature_names)
""")
    add("markdown", """
## 4. Smoke test với 365 ngày đầu, tối đa 20 boosting rounds

XGBoost dùng boosting rounds, không dùng epoch như mạng CNN. Smoke kiểm tra training,
calibration, threshold, biểu đồ, lưu/load và inference; không mở Test.
Baseline: dự đoán khô, tỷ lệ giáng thủy Train và trạng thái giáng thủy giờ trước.
""")
    add("code", """
SMOKE_OK = False
smoke_table = table.loc[table.index < table.index.min() + pd.Timedelta(days=365)]
SMOKE_RUN = OUTPUT_ROOT / f"smoke-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')}"
train(smoke_table, feature_names, SMOKE_RUN, feature_config=feature_config,
      config=TrainConfig(num_boost_round=20, early_stopping_rounds=5, smoke=True), provenance=source)
smoke_metrics = json.loads((SMOKE_RUN / "validation_metrics.json").read_text(encoding="utf-8"))
print(json.dumps(smoke_metrics, indent=2))
display(DisplayImage(filename=str(SMOKE_RUN / "history.png")))
display(DisplayImage(filename=str(SMOKE_RUN / "validation.png")))
predictor = RainPredictor(SMOKE_RUN)
reference = smoke_table.index[-1]
print(predictor.predict(hourly.loc[:reference], as_of=reference, source="open_meteo_archive"))
SMOKE_OK = True
print("Smoke thành công; chưa chạy đầy đủ hoặc đánh giá Test.")
""")
    add("markdown", """
## 5. Huấn luyện đầy đủ — bật sau smoke và rà soát

Đọc định nghĩa nhãn đại diện cho mưa và hạn chế nguồn trước khi bật.
Không có API/pump action trong pipeline. Mỗi run dùng thư mục mới, model lưu UBJ,
calibration và preprocessing lưu JSON. XGBoost CPU chạy nhanh; nếu ngắt trước khi xuất
artifact, tạo run mới và chạy lại với cùng seed. Không tự coi run dở là model hợp lệ.
""")
    add("code", """
RUN_FULL_TRAINING = False
DATA_REVIEWED = False
FULL_RUN = None
if RUN_FULL_TRAINING:
    if not SMOKE_OK or not DATA_REVIEWED:
        raise RuntimeError("Cần smoke thành công và rà soát dữ liệu trước")
    FULL_RUN = OUTPUT_ROOT / f"xgboost-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')}"
    train(table, feature_names, FULL_RUN, feature_config=feature_config,
          config=TrainConfig(seed=int(os.getenv("WEATHER_SEED", "42"))), provenance=source)
    print(json.dumps(json.loads((FULL_RUN / "validation_metrics.json").read_text(encoding="utf-8")), indent=2))
    display(DisplayImage(filename=str(FULL_RUN / "history.png")))
    display(DisplayImage(filename=str(FULL_RUN / "validation.png")))
else:
    print("Full training đang tắt.")
""")
    add("markdown", """
## 6. Test cuối cùng — không dùng để điều chỉnh hyperparameter

Chỉ mở sau khi chọn cấu hình. Nếu run đã có test_metrics.json, đọc lại báo cáo đã lưu.
Muốn đọc một run hoàn tất sau restart, gán FULL_RUN tới thư mục đó trên Drive.
""")
    add("code", """
FINAL_EVALUATION = False
if FINAL_EVALUATION:
    if FULL_RUN is None:
        raise RuntimeError("Chưa có run đầy đủ")
    result = evaluate_test(table, FULL_RUN, allow_test=True)
    print(json.dumps(result, indent=2))
    display(DisplayImage(filename=str(FULL_RUN / "test.png")))
else:
    print("Test đang khóa.")
""")
    add("markdown", """
## 7. Inference hồi cứu và tích hợp sau này

`as_of` phải đúng giờ UTC, có đủ 6 giờ lịch sử và không chứa dữ liệu tương lai.
Ví dụ dưới là **phát lại lịch sử**, không phải dự báo thời tiết hôm nay.
OpenWeather cần các snapshot đã nhận trước từng mốc giờ, dữ liệu mới <=15 phút,
`units=metric`, áp suất `grnd_level`; không dùng `pressure` mực biển thay áp suất mặt đất.
Chế độ OpenWeather mặc định bị chặn vì chưa kiểm chứng domain shift. Xem README để thử adapter.
Chưa tích hợp vào backend; backend vẫn Mock AI và AI không trực tiếp điều khiển bơm.
""")
    add("code", """
ARTIFACT_DIR = FULL_RUN if FULL_RUN is not None else SMOKE_RUN
predictor = RainPredictor(ARTIFACT_DIR)
reference = table.index[-1]
prediction = predictor.predict(hourly.loc[:reference], as_of=reference, source="open_meteo_archive")
print(json.dumps(prediction, ensure_ascii=False, indent=2))
print("Artifact:", ARTIFACT_DIR)
""")
    return {"cells": cells, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    destination = Path(__file__).resolve().parents[1] / "notebooks" / "train_xgboost_rain.ipynb"
    if destination.exists() and not args.force:
        if json.loads(destination.read_text(encoding="utf-8")).get("cells"):
            raise SystemExit("Notebook đã có nội dung; không ghi đè nếu thiếu --force")
    destination.parent.mkdir(parents=True, exist_ok=True)
    notebook = build_notebook()
    temporary = destination.with_suffix(".ipynb.tmp")
    temporary.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    temporary.replace(destination)
    print(f"{destination}: {len(notebook['cells'])} cells")
