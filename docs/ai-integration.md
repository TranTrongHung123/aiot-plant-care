# Tích hợp model đã huấn luyện — 15/10/2026

Backend chạy inference thật bằng CPU, dùng artifact local trong `model-artifacts/`.
Thiết bị vẫn là simulator, namespace MQTT `simulation`; AI không trực tiếp kích hoạt bơm.
Từ 16/10/2026, dự báo mưa LIVE là một điều kiện an toàn cho tưới theo lịch;
DEMO dùng kịch bản có mưa/không mưa riêng. Xem [chế độ thời tiết và lịch tưới](weather-modes.md).
Không có fallback sang kết quả Mock. Lịch sử Mock cũ được giữ nguyên và có nhãn;
job Mock đang chờ sẽ thất bại với yêu cầu gửi job mới.

## Artifact và runtime

| Task | Biến môi trường     | Phiên bản hiện tại                    |
| ---- | ------------------- | ------------------------------------- |
| Lá   | `LEAF_MODEL_DIR`    | `mobilenetv3-20261015T031708_509896Z` |
| Quả  | `FRUIT_MODEL_DIR`   | `full-20261015T040727_579719Z`        |
| Mưa  | `WEATHER_MODEL_DIR` | `xgboost-20261015T035744_305966Z`     |

Đường dẫn tương đối tính từ gốc repository, độc lập thư mục chạy lệnh; có thể dùng
đường dẫn tuyệt đối. Giá trị mặc định trỏ đến ba thư mục trên. Không tự chọn model
mới nhất. Khi thay model, cấu hình lại đường dẫn và khởi động lại API/worker.
Job lưu version ngay lúc tạo; worker từ chối xử lý nếu artifact đã đổi version.

`AI_PROVIDER=trained`. Giá trị `mock` còn sót trong `.env` cũ được chuyển thành
`trained` để nâng cấp tương thích; không còn mã thực thi Mock. Đổi dòng này khi cập nhật
cấu hình, các bí mật khác giữ nguyên. Weights/artifact bị Git ignore.

Runtime trong `backend/.venv`: XGBoost 3.0.5, Ultralytics 8.3.203,
Torch 2.8.0 CPU + torchvision 0.23.0 theo
[cặp phiên bản chính thức](https://pytorch.org/get-started/previous-versions/).
Colab đã train với Torch 2.11.0 CUDA; backend nạp state dict/checkpoint bằng CPU,
không train và không cài CUDA. `uv.lock` khóa dependency cho backend.

Adapter `app/modules/trained_ai.py` tái sử dụng predictor/preprocessing từ `ml/`;
khi triển khai cần giữ cả `backend/`, `ml/` và thư mục artifact theo cấu hình.
API chỉ đọc metadata, không import Torch và không nạp weights trong request.
Worker nạp/cache một model mỗi task, kiểm tra checksum trước deserialize.
Chỉ dùng checkpoint do mình tạo/kiểm soát; checksum phát hiện hỏng file,
không chứng minh checkpoint từ nguồn lạ an toàn.

Worker dùng process con cùng codebase để giới hạn inference 120 giây, lease job
180 giây. Process con bị dừng khi timeout và được tạo lại cho job tiếp theo.
Lỗi storage/timeout được retry tối đa 3 lần; thiếu model/checksum/input sai trả
failed có lý do ngay. Không dùng Redis/Celery hoặc thêm dịch vụ ML riêng.

## OpenWeather trực tuyến

Điền trong `.env` gốc, không gửi API key trong chat hay đưa vào Git:

```dotenv
AI_PROVIDER=trained
OPENWEATHER_API_KEY=your-key
WEATHER_LATITUDE=your-latitude
WEATHER_LONGITUDE=your-longitude
WEATHER_ALLOW_DOMAIN_SHIFT=true
```

Worker gọi [Current Weather API](https://openweathermap.org/api/current) qua HTTPS
mỗi 60 giây khi đủ cấu hình, với `units=metric`. Lưu nhiệt độ °C, độ ẩm %, áp suất
mặt đất `main.grnd_level` hPa, mây %, gió m/s. Không dùng `pressure` mực biển thay
`grnd_level`. `dt` được lưu làm `measured_at`, thời điểm nhận làm `received_at` UTC.
Không log URL chứa API key. HTTP lỗi/thiếu trường/quan trắc cũ không được biến thành
giá trị giả. Lỗi HTTP được ghi mã trạng thái vào worker log.

Alembic `c71a02` thêm `weather_observations`. Dữ liệu được tách theo tọa độ cấu hình;
đổi địa điểm phải tích lũy lịch sử mới. API key/tọa độ trống không cản inference ảnh.

Model cần **6 mốc giờ liên tiếp**. Tại mỗi mốc chỉ dùng snapshot đã đo và nhận
trước mốc đó, tuổi không quá 15 phút. Vì thế sau khi cấu hình thường cần chờ khoảng
6 giờ và giữ worker chạy liên tục; dữ liệu hợp lệ được lưu qua restart. Khoảng trống
hoặc quan trắc OpenWeather quá cũ có thể khiến phải chờ lâu hơn.

Model được train trên lưới giờ: chạy dự báo trong **15 phút đầu giờ**, horizon là
`(mốc giờ, mốc giờ + 60 phút]`, không phải 60 phút tính từ lúc bấm nút.
UI hiển thị khoảng thời gian dự báo; ẩn xác suất khi hết hạn. Nút bị khóa kèm lý do
khi thiếu cấu hình/lịch sử hoặc ngoài cửa sổ. `POST /api/v1/ai/weather` không có body;
trả 409 nếu chưa đủ dữ liệu, 202 khi đã xếp job. Job lưu snapshot đầu vào để truy vết.
Không lấy telemetry simulator, dữ liệu dự báo của nhà cung cấp hoặc dữ liệu tương lai
để bù lịch sử thiếu. Không tự tải dữ liệu lịch sử bằng gói trả phí.

`WEATHER_ALLOW_DOMAIN_SHIFT=true` cho phép thử nghiệm theo lựa chọn OpenWeather.
Train dùng Open-Meteo archive tái dựng; chưa đánh giá sai lệch sang OpenWeather.
Kết quả giữ `domain_shift=true`, `production_ready=false`, ngưỡng đã học ~0.37,
calibration từ artifact và định nghĩa nhãn `precipitation[t+1h] >= 0.1 mm`.
Đặt biến này false để chặn inference OpenWeather nhưng vẫn thu thập quan trắc.

## Ảnh và kết quả

Upload JPEG/PNG → kiểm tra/chuẩn hóa → MinIO private → job PostgreSQL → worker.
MobileNetV3 dùng RGB/EXIF, resize cạnh ngắn 232, center crop 224 và ImageNet normalize;
trả top-3 của 10 lớp. Confidence softmax chưa calibration/OOD: ảnh không phải lá vẫn
có thể có điểm cao. YOLO dùng letterbox 640, confidence 0.25, NMS IoU 0.7, tối đa
300 hộp; lớp `ripe`/`unripe`, không nhận bệnh quả. Không phát hiện được quả trả count=0.

Kết quả có `provider=trained`, `is_mock=false`, version, thời điểm inference,
`source`/`input_source`, confidence tương ứng. Hộp YOLO gốc `xyxy` được giữ lại,
adapter thêm `box=[x,y,width,height]` chuẩn hóa [0,1] cho overlay dashboard.
Ảnh simulator vẫn mang source simulation, dù inference là model thật.

Thiếu artifact khi upload trả 503; artifact hỏng hoặc inference lỗi tạo job failed.
`dashboard.models` báo `configured` khi đủ metadata/file, không coi đó là bằng chứng
model đã load; thành công của job mới chứng minh inference thực sự đã chạy.

## Chạy và kiểm tra

```powershell
cd backend
uv sync --locked
uv run alembic upgrade head
uv run ruff check app tests migrations
uv run pytest -q
cd ..
powershell -ExecutionPolicy Bypass -File scripts/start-demo.ps1 -Restart
```

Xem [demo.md](demo.md) cho E2E backend và Playwright chạy tuần tự.
Test artifact thật tự skip nếu máy không có weights (weights không lưu Git).
Fixture ảnh tổng hợp/sáu giờ thời tiết trong test chỉ kiểm tra phần mềm, không
đánh giá độ chính xác và không thay dữ liệu OpenWeather trực tuyến.
Cần bổ sung đánh giá ảnh ESP32-CAM và sai lệch nguồn thời tiết trước khi khẳng định
chất lượng ngoài dataset.

## Các file chính thay đổi

- `backend/app/modules/trained_ai.py`: thay provider Mock bằng adapter ba model.
- `backend/app/workers/inference.py`, `runtime.py`: pin version, timeout và xử lý lỗi.
- `backend/app/integrations/weather.py`: thu thập/kiểm tra quan trắc OpenWeather;
  `storage.py`: áp dụng EXIF trước khi chuẩn hóa ảnh.
- `backend/app/api/routes.py`, `core/settings.py`, `db/models.py`, `main.py`:
  cấu hình, xếp job, trạng thái model và quan trắc; migration `c71a02`.
- `backend/pyproject.toml`, `uv.lock`: runtime CPU có phiên bản cố định.
- `frontend/src/app/page.tsx`, `layout.tsx`, `components/dashboard.tsx`, `lib/api.ts`:
  nhãn model thật/lịch sử Mock, phiên bản, confidence, overlay và trạng thái mưa.
- `ml/weather/weather_training/calibration.py`, `inference.py`, `train.py`:
  tách phép calibration chung để inference không import pipeline training.
- Test backend/Playwright, `.env.example`, `.gitignore`, README và tài liệu vận hành
  được cập nhật cùng thay đổi.

## Kết quả kiểm chứng — 15/10/2026

- `uv sync --locked`, Ruff: đạt. `RUN_E2E=1 uv run pytest -q`: **24 passed**,
  gồm artifact thật trên CPU, upload private, hướng EXIF, hộp YOLO, thiếu/hỏng model,
  dữ liệu mưa thiếu/tương lai, timeout process, lease/retry, job Mock cũ và an toàn
  điều khiển simulation. Không chạy training trên Windows.
- Migration `c71a02` ở head; `alembic check` không phát hiện lệch schema.
- Frontend: `npm ci`, lint và production build đạt; Playwright **1 passed**,
  kiểm tra ảnh lá/quả, overlay, lịch sử sau reload, banner model thật và mobile.
  Đã xem ảnh chụp kiểm chứng tại `.runtime/` (Git ignore).
- Compose config hợp lệ; PostgreSQL/MinIO/MQTT healthy, init bucket exit 0.
- `npm audit --omit=dev`: 0 cảnh báo. `npm ci` vẫn báo 5 cảnh báo high trong dependency
  phát triển hiện có; không nâng major dependency ngoài phạm vi tích hợp AI.
- Chưa gọi OpenWeather trực tuyến vì chưa có key/tọa độ cấu hình; chưa tích lũy đủ
  6 mốc giờ quan trắc thật. XGBoost được kiểm tra bằng dữ liệu đầu vào fixture có nhãn
  kiểm thử, không coi đó là đánh giá dự báo thực tế. Chưa đánh giá ESP32-CAM/HiveMQ/bơm thật.
