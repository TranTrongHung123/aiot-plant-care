# Huấn luyện AI trên Google Colab

Cả ba bài toán huấn luyện trên **hosted runtime Google Colab**, không train trên Windows.
Laptop chỉ chỉnh sửa/đóng gói mã nguồn và chạy backend/frontend demo. Backend giữ
`backend/.venv`, Python 3.12 + uv; chưa thay worker Mock AI bằng model thật.

## Chuẩn bị mã nguồn và dữ liệu

Từ gốc repository trong PowerShell, không cần cài thư viện ML trên laptop:

```powershell
powershell -NoProfile -File scripts/package-colab.ps1
# Nếu muốn đóng gói luôn hai dataset hiện có (không upload tự động):
powershell -NoProfile -File scripts/package-colab.ps1 -IncludeDatasets
```

Kết quả mã nguồn: `ml/dist/aiot-ml-colab.zip` (cả ba pipeline, notebook, helper và lock).
Script dùng Python stdlib qua uv, không train và không đưa `.env`, dataset, weights,
outputs hoặc `.venv` vào source ZIP. Archive đã tồn tại cùng nội dung được dùng lại;
nếu nội dung khác, script từ chối ghi đè. Dùng `--output` tên mới khi đóng bản cập nhật:

```powershell
uv run --no-project --python 3.12 ml/scripts/package_colab.py --output ml/dist/aiot-ml-colab-v2.zip
```

Upload thủ công lên `MyDrive/aiot-plant-care/`:

| File                     | Dùng cho                                                                        |
| ------------------------ | ------------------------------------------------------------------------------- |
| `aiot-ml-colab.zip`      | Source chung; nếu dùng tên v2, đổi `SOURCE_ZIP` trong bootstrap                 |
| `plantvillage.zip`       | Lá; tùy chọn `-IncludeDatasets` tạo từ `ml/leaf-disease/data/raw/plantvillage/` |
| `tomato-ripeness-v4.zip` | Quả; tùy chọn trên tạo từ `ml/fruit-health/data/raw/tomato-ripeness-v4/`        |
| `open-meteo.csv`         | Upload trực tiếp `ml/weather/data/raw/open-meteo.csv`                           |

Không cần gửi dataset lên Git. Có thể thay ZIP lá bằng KaggleHub, ZIP quả bằng Roboflow
hoặc CSV bằng Open-Meteo download ngay trong notebook. Kaggle có thể cần đăng nhập;
Roboflow dùng Colab Secrets `ROBOFLOW_API_KEY`, không ghi key vào cell/output.

## Mở notebook và chạy

Upload notebook tương ứng tại [Google Colab](https://colab.research.google.com/),
hoặc dùng extension Colab trong VS Code với **hosted runtime**, không chọn interpreter Windows.
Notebook trên laptop không tự đưa module Python hoặc dữ liệu lên Colab; cần source ZIP trên Drive.

| Notebook                                         | Runtime   | Cấu hình khởi đầu                                                      |
| ------------------------------------------------ | --------- | ---------------------------------------------------------------------- |
| `leaf-disease/notebooks/train_mobilenetv3.ipynb` | GPU       | MobileNetV3-Large, crop 224, batch 32, workers 2, AMP, tối đa 20 epoch |
| `fruit-health/notebooks/train_yolo_fruit.ipynb`  | GPU       | YOLO11n, 640×640, batch 16, workers 2, AMP, tối đa 80 epoch            |
| `weather/notebooks/train_xgboost_rain.ipynb`     | CPU Colab | XGBoost hist, 2 threads, tối đa 500 rounds                             |

Lock bổ sung nhắm **Python 3.12**, runtime có **torch >=2.6 và torchvision tương thích**.
Bootstrap kiểm tra phiên bản, không tự thay Torch/CUDA; nếu runtime khác Python 3.12,
dùng runtime tương thích hoặc cập nhật và kiểm chứng lock cho phiên bản mới trước khi train.
Không giả định GPU được cấp luôn là T4: đọc tên/VRAM trong output. Nếu OOM, giảm batch
một nửa, restart session và dùng run mới; không tự đổi batch khi resume.

1. Chọn runtime thích hợp, chạy bootstrap và cho phép mount Google Drive.
2. Bootstrap cài dependency bổ sung. Nếu báo module đã cũ trong bộ nhớ, chọn
   **Runtime > Restart session**, rồi chạy lại từ đầu. Dependency đúng lock sẽ không cài lại.
3. Đọc dữ liệu, audit và chia tập. Dữ liệu ảnh được giải nén vào `/content/aiot-data/`,
   dùng marker SHA256 để không trộn ZIP khác hoặc bản giải nén dở. Không train trực tiếp hàng nghìn ảnh qua Drive.
4. Chạy smoke trước, rà soát audit/phân bố. Chủ động bật các cờ full training và xác nhận dữ liệu.
5. Chỉ mở Test sau khi chốt cấu hình bằng Validation. **Run All mặc định không train đầy đủ/Test.**

## Lưu kết quả và tiếp tục phiên bị ngắt

Run lưu trên Drive: `leaf-runs/`, `fruit-runs/`, `weather-runs/` dưới `aiot-plant-care/`.
Lá lưu `last.pt`/`best.pt` mỗi epoch; quả lưu `yolo/weights/last.pt`/`best.pt`.
Sau khi đổi runtime: mount Drive, bootstrap đúng source, khôi phục cùng dataset,
chọn run cũ trong cell resume. Giữ nguyên manifest/cấu hình; không dùng weights smoke cho full train.
YOLO prepared-full nằm ở đường dẫn cố định trong `/content/aiot-data/` để resume
không phụ thuộc thư mục source có hash. Không resume run YOLO đã hoàn tất/strip optimizer.
XGBoost chưa resume giữa boosting rounds: nếu chưa xuất artifact, tạo run mới cùng seed.

Drive vẫn có thể lỗi quota/I/O hoặc mất lần ghi cuối khi ngắt giữa epoch. Runtime `/content`
có thể bị xóa và GPU không được bảo đảm; xem [Colab FAQ](https://research.google.com/colaboratory/faq.html).
Giữ cả thư mục artifact cùng metadata/checksum, không chỉ weights. Tải artifact đã đánh giá
về laptop để tích hợp inference ở worker backend sau; hiện backend vẫn dùng **Mock AI**.

## Dependency, kiểm thử và dọn môi trường cũ

- `requirements-colab.in` và `.lock`: dependency bổ sung chung, gồm dependency bắc cầu.
- `requirements-runtime.in`: loại Torch/torchvision/NCCL khỏi resolver; chúng do Colab quản lý.
- Bootstrap dùng `pip install --no-deps -r requirements-colab.lock`, xác nhận Torch không đổi,
  kiểm tra dependency cần thiết và torchvision NMS. Không dùng lock này để tạo runtime từ rỗng.
- Runtime đầy đủ không được khóa bởi lock bổ sung: giữ thông tin phiên bản trong artifact;
  thay runtime có thể thay kết quả. Nếu bootstrap báo dependency xung đột, không tiếp tục train.
- `requirements-dev.in/.lock`: thêm pytest, Ruff, nbformat để kiểm tra code trên Colab.

Tái tạo lock trên máy phát triển bằng uv (chỉ resolve, không train):

```powershell
cd ml
uv pip compile requirements-colab.in --excludes requirements-runtime.in --python-version 3.12 --python-platform linux -o requirements-colab.lock
uv pip compile requirements-dev.in --excludes requirements-runtime.in --python-version 3.12 --python-platform linux -o requirements-dev.lock
```

Sau bootstrap, kiểm thử trong **Colab** từ `ML_ROOT` bằng subprocess của kernel:

```python
import subprocess, sys
subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--no-deps', '-r', str(ML_ROOT / 'requirements-dev.lock')])
subprocess.check_call([sys.executable, '-m', 'ruff', 'check', '.'], cwd=ML_ROOT)
subprocess.check_call([sys.executable, '-m', 'pytest', '-q'], cwd=ML_ROOT)
```

Các `.venv` ML trên laptop là môi trường cũ, không còn là luồng train được hỗ trợ.
Không cần xóa dataset, outputs, weights hoặc `backend/.venv` khi chuyển sang Colab.
Xem trước dọn cache bằng `scripts/clean-generated.ps1`; thêm `-RemoveLocalMlEnvironments`
để đưa cả bốn `.venv` ML cũ vào danh sách, thêm `-Apply` để thực hiện sau khi dừng Python.
Script không còn yêu cầu CUDA local và giữ ZIP upload, dữ liệu, kết quả thí nghiệm.

Chưa có phiên hosted Colab được kết nối trong lần cập nhật này; kiểm tra tự động bootstrap/ZIP
không thay thế việc chạy smoke trên runtime Colab thực tế. Chưa chạy full training hoặc mở Test.

Kiểm chứng chuyển Colab ngày 15/10/2026: Ruff sạch, 17 kiểm thử bootstrap/ZIP đạt,
ba notebook hợp lệ và khớp nguồn tạo notebook. Kiểm thử bao gồm từ chối kernel Windows,
giả lập mount/extract/chạy lại, giữ Torch khi cài dependency, yêu cầu restart khi module cũ,
từ chối ZIP vượt thư mục và không ghi đè dataset khác. Đây là kiểm tra code, không train trên Windows.

## Ba bài toán AI — phạm vi chính

| Thư mục         | Mô hình                        | Đánh giá tối thiểu dự kiến                               |
| --------------- | ------------------------------ | -------------------------------------------------------- |
| `weather/`      | XGBoost, mưa trong 60 phút tới | Split theo thời gian, PR-AUC/F1, calibration và baseline |
| `leaf-disease/` | MobileNetV3, bệnh lá cà chua   | Macro F1, confusion matrix, ảnh ESP32-CAM giữ riêng      |
| `fruit-health/` | YOLO, phát hiện và độ chín quả | mAP, precision/recall từng lớp, sai số đếm               |

`fruit-health` giữ tên thư mục hiện có; nhận diện bệnh quả chỉ là mở rộng.
Mỗi bài toán cần README nguồn/giấy phép dữ liệu, nhãn, cách chia tập, preprocessing,
seed, môi trường training, metrics, version và giới hạn model trước khi triển khai.
Dataset và weights nằm ngoài Git; artifact có checksum và thông tin nguồn.

Weather phải chốt ngưỡng lượng mưa, quy ước khoảng thời gian của nhãn và đặc trưng
thực sự có sẵn tại lúc dự đoán. Không trộn Open-Meteo lịch sử với OpenWeather trực tuyến
mà bỏ qua khác biệt độ phân giải, đơn vị và sai lệch nguồn. Cảm biến tại cây không thay
thế tự động bộ đặc trưng thời tiết dùng khi train. Chưa có dữ liệu thì báo chưa có model.

Đã có pipeline/notebook độc lập cho [bệnh lá MobileNetV3](leaf-disease/README.md)
và [dự báo giáng thủy 60 phút XGBoost](weather/README.md), cùng
[phát hiện/độ chín quả YOLO11n](fruit-health/README.md), có smoke test và artifact inference.
Chưa thay Mock AI của backend hoặc xác nhận chất lượng vận hành thực tế.

# Dataset nguồn

https://www.kaggle.com/datasets/charuchaudhry/plantvillage-tomato-leaf-dataset

https://universe.roboflow.com/uni-zkvyc/tomato-ripeness-um0yl/dataset/4

https://open-meteo.com/en/docs/historical-weather-api?latitude=20.96&longitude=105.76&timezone=Asia%2FBangkok&start_date=2021-01-01&end_date=2026-10-10&hourly=temperature_2m,relative_humidity_2m,precipitation,surface_pressure,cloud_cover,wind_speed_10m&models=ecmwf_ifs
