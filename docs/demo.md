# Chạy demo End-to-End với AI đã huấn luyện

## Chạy nhanh trên Windows

Yêu cầu: Docker Desktop đang chạy Linux containers, uv, Node.js/npm. Hạ tầng dùng
PostgreSQL, MinIO và Mosquitto local; API, worker, simulator và Next.js chạy trên host.

Từ thư mục gốc:

```powershell
# Lần đầu nếu chưa có môi trường:
if (!(Test-Path .env)) { Copy-Item .env.example .env }
# Sửa mật khẩu mẫu và APP_TOKEN trong .env; APP_TOKEN tối thiểu 16 ký tự.
docker compose build minio
powershell -ExecutionPolicy Bypass -File scripts/start-demo.ps1
```

Với môi trường đã cài dependency và build frontend:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-demo.ps1 -SkipInstall
```

Nếu demo đang chạy và các tiến trình vẫn đúng phiên được quản lý, chạy lại lệnh sẽ
dùng phiên hiện có và in địa chỉ dashboard. Để cài/build lại sau khi sửa code:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-demo.ps1 -Restart
```

Chỉ khởi động lại bằng bản build hiện có: thêm `-Restart -SkipInstall`.
Nếu cổng bị tiến trình khác chiếm, script báo PID để kiểm tra; không tự dừng tiến trình đó.

- Dashboard: <http://localhost:3000>. Đăng nhập bằng `APP_TOKEN` trong `.env` gốc.
  Phiên khởi tạo đã tạo khóa ngẫu nhiên; không commit hoặc đưa khóa này vào frontend.
- Swagger: <http://localhost:8000/docs>. Nhấn Authorize và nhập cùng token.
- MinIO Console: <http://localhost:9001>, tài khoản root từ `.env`.
- Log từng tiến trình: `.runtime/{api,worker,simulator,web}.{out,err}.log`.
- Script cố định cổng API 8000, frontend 3000 và bind loopback, dành riêng demo local.
  `BACKEND_PORT` trong mẫu env cũ không được script này sử dụng; nếu đổi port, sửa
  script và `frontend/.env.local` đồng bộ rồi build lại frontend.

Script kiểm tra cổng, khởi động Docker Desktop nếu cần, chờ DB/storage/broker healthy,
chạy init bucket và Alembic,
sau đó khởi động các tiến trình ẩn. Một database chỉ cho một API process giữ
MQTT consumer/scheduler (PostgreSQL advisory lock).

```powershell
powershell -ExecutionPolicy Bypass -File scripts/stop-demo.ps1
```

Script dừng đúng cây tiến trình đã tạo, kiểm tra PID + thời điểm tạo + command line.
Docker và dữ liệu vẫn được giữ. Sau khi đổi code, dừng demo rồi chạy script khởi động
lại không có `-SkipInstall` để build frontend mới. Không build đè `.next` khi đang demo.
Nếu Docker/DB bị khởi động lại giữa phiên, dừng rồi chạy lại demo để thiết lập lại
khóa singleton API và các kết nối. Khi DB không sẵn sàng, REST trả 503 có thông báo;
WebSocket báo service_unavailable và client kết nối lại. Kết nối DB có timeout 3 giây,
SQL statement có giới hạn 10 giây để tránh giữ request vô hạn.

## Kịch bản trình diễn

Giao diện được chia theo nghiệp vụ; xem [sitemap và thiết kế frontend](frontend-design.md).
Chọn kịch bản tại **Mô phỏng**, gửi lệnh tại **Điều khiển tưới**, cấu hình quy tắc
và DEMO/LIVE tại **Lịch tưới**. **Tổng quan** chỉ hiển thị tình trạng và liên kết
đến các trang này. **Phân tích AI** và **Lịch sử tưới** có URL riêng.

1. Đăng nhập. Simulator gửi telemetry mỗi 5 giây qua MQTT; dashboard nhận snapshot
   WebSocket mỗi 2 giây. Dữ liệu được lưu thật trong PostgreSQL. REST polling 8 giây
   là dự phòng khi WebSocket lỗi. Khi mất API hoặc dữ liệu cũ, UI hiển thị rõ trạng thái.
2. Chọn **Đất khô**. Độ ẩm đất về khoảng 22% sau tối đa một chu kỳ telemetry.
3. Nhấn **Tưới ngay** (1–15 giây). Lịch sử chuyển Đã gửi → Đã nhận ACK → Hoàn tất.
   **Dừng bơm** ưu tiên kết thúc chu kỳ sớm. Relay ON chỉ là trạng thái mô phỏng.
4. Chọn thời tiết **DEMO → Có mưa**. Bật tự động theo lịch, chọn chu kỳ 1 phút,
   ngưỡng 30%, thời lượng 1 giây và nghỉ 30 giây. Đến lịch sẽ bỏ qua vì mưa.
   Chuyển **Không mưa**, đợi lượt kế tiếp: chỉ tưới nếu đất khô và đủ điều kiện an toàn.
   Có thể đặt nhiều giờ cố định hằng ngày theo giờ Việt Nam. Tắt tự động khi kết thúc demo.
   Xem chi tiết [chế độ thời tiết và lịch tưới](weather-modes.md).
5. Chọn **Mất kết nối** để ngừng telemetry và từ chối điều khiển ON. Simulator vẫn
   nghe topic cấu hình để có thể chọn **Bình thường** và phục hồi từ giao diện.
   Đây là kịch bản offline mô phỏng, không cắt kết nối TCP của broker.
6. Chọn **LIVE** để dùng XGBoost với OpenWeather thật theo [tích hợp AI](ai-integration.md).
   Worker tự thu thập và tạo dự báo khi đủ 6 mốc giờ; có thể yêu cầu dự báo trong
   15 phút đầu giờ. Thiếu dự báo còn hạn sẽ chặn tưới tự động. DEMO không cần chờ dữ liệu;
   kịch bản **Trời mưa** của simulator không thay đổi đầu vào XGBoost.
7. Trong **Phân tích AI**, tải JPEG/PNG cho **Bệnh lá** hoặc **Độ chín**. Ảnh được
   kiểm tra nội dung, chuẩn hóa JPEG, lưu private vào MinIO; worker đọc ảnh và ghi
   kết quả MobileNetV3/YOLO thật với version, confidence và hộp phát hiện.
   Không có quả được phát hiện thì count=0, không tạo hộp giả.
8. Tải lại trang, đăng nhập lại và kiểm tra lịch sử ảnh/kết quả/lệnh vẫn còn.

Có ảnh fixture tổng hợp tại `frontend/tests/fixtures/tomato-demo.png` để thử upload.
Ảnh này chỉ phục vụ kiểm thử phần mềm, không phải mẫu để đánh giá mô hình thật.

## Phạm vi hiện tại

PostgreSQL, MinIO, MQTT, FastAPI và Next.js chạy thật. Thiết bị duy nhất là
`sim-esp32-01`, namespace simulation. AI dùng weights đã huấn luyện; không còn
Mock provider. Lịch sử Mock cũ được giữ nguyên và ghi nhãn, không đổi thành model thật.
Ở LIVE, dự báo mưa AI tham gia kiểm tra điều kiện tưới theo lịch; AI không trực tiếp
điều khiển bơm. DEMO dùng kịch bản mưa được ghi nhãn rõ. `DEVICE_MODE` phải là simulation.
Cấu hình/giới hạn model, OpenWeather và runtime CPU: [ai-integration.md](ai-integration.md).
Chưa tích hợp HiveMQ Cloud/TLS hay firmware điều khiển vật lý.

## Thiết kế và API hiện có

- `app/core`: settings và Pydantic contract; `app/db`: ORM; `migrations`: Alembic.
- `app/modules/control.py`: safety, idempotency, policy; `trained_ai.py`: adapter model đã huấn luyện.
- `app/integrations`: MQTT và MinIO; `app/workers`: inference và simulator.
- Worker lấy job bằng `FOR UPDATE SKIP LOCKED`, lease 180 giây, inference timeout 120 giây, tối đa 3 lần thử,
  trả failed có lý do khi storage lỗi; job lease hết hạn được worker nhận lại.
- Simulator ghi ID/ACK vào SQLite local (`backend/.simulator.sqlite3`) để chống replay
  qua restart; không khôi phục bơm ON. Đây là state của thiết bị giả, không thay DB hệ thống.
- Lệnh hết hạn/duplicate được chặn; dữ liệu quá 20 giây chặn ON. Timeout ACK không
  đồng nghĩa OFF. Simulator tắt relay khi mất MQTT hoặc hết duration tại chỗ.
- Simulator bỏ qua JSON sai định dạng, payload quá 16 KiB, bản tin retained và topic
  ngoài namespace simulation. Bản tin command phải có schema_version=1 và UUID hợp lệ.
- Schema demo: devices, sensor_readings, irrigation_commands (kèm event history),
  irrigation_policies, images, inference_jobs (kèm prediction và version).
  Chưa tách users, plants, predictions/model registry thành bảng riêng ở quy mô một cây.

| API                                       | Tác dụng                                             |
| ----------------------------------------- | ---------------------------------------------------- |
| `GET /health`                             | Kiểm tra API/DB và trạng thái MQTT                   |
| `GET /api/v1/dashboard`                   | Snapshot, 60 telemetry, 30 lệnh và 30 job gần nhất   |
| `POST /api/v1/irrigation/commands`        | ON/OFF có command UUID và duration                   |
| `PUT /api/v1/irrigation/policy`           | Cấu hình ngưỡng/cooldown/tự động                     |
| `POST /api/v1/simulation/scenario`        | normal/dry/rain/offline                              |
| `POST /api/v1/ai/weather`                 | Tạo job mưa từ 6 mốc giờ OpenWeather                         |
| `POST /api/v1/images?purpose=leaf\|fruit` | Multipart `file`, tối đa 5 MiB/16 MP                 |
| `GET /api/v1/images/{id}/content`         | Ảnh qua API có xác thực                              |
| `GET /api/v1/jobs/{id}`                   | Trạng thái/kết quả job                               |
| `POST /api/v1/jobs/{id}/retry`            | Chạy lại job failed                                  |
| `WS /ws`                                  | Frame đầu `{ "token": "..." }`, sau đó nhận snapshot |

## Phát triển thủ công

Trong `backend/`, chạy mỗi lệnh ở terminal riêng sau khi `uv sync --locked` và
`uv run alembic upgrade head`:

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
uv run python -m app.workers.inference
uv run python -m app.workers.simulator
```

Trong `frontend/`: `npm ci`, `npm run dev`. Không chạy song song API này với demo
đang giữ lock. Simulator tại backend dùng chung contract và env; các thư mục
`simulators/esp32`, `simulators/camera` dành cho tách adapter khi tích hợp phần cứng.

## Kiểm thử

```powershell
cd backend
uv run ruff check app tests migrations
uv run pytest -q
# Sau khi demo đang chạy (tạo dữ liệu simulation trong DB):
$env:RUN_E2E='1'
uv run pytest -q
Remove-Item Env:RUN_E2E
cd ../frontend
npm run lint
npm run build
npx playwright install chromium
npm run test:e2e
```

Test E2E backend và trình duyệt chạy tuần tự, không đồng thời vì cùng thay đổi kịch bản
của một simulator. Test trả policy/kịch bản về trạng thái trước hoặc bình thường và
gửi OFF sau khi kiểm tra. Không chạy trên thiết bị thật.

Giới hạn demo: một người dùng dùng chung token, chưa có account/role, pagination lịch sử,
retention, camera credential riêng, TLS public hay đánh giá model trên camera thực tế. `npm audit --omit=dev`
không có cảnh báo ở lần kiểm tra; dependency lint hiện có chuỗi cảnh báo braces/fast-glob
trong dev tools. Không hạ major Next.js tự động chỉ để xóa cảnh báo của lint tool.

## Kết quả kiểm chứng bản Mock E2E — 13/10/2026

- Backend: **15 tests passed** (11 unit + 4 E2E), gồm auth/WebSocket, ba loại job,
  ảnh không hợp lệ/private, ACK, idempotency, expiry, dữ liệu cũ, offline, tưới tự động,
  bản tin malformed/retained/sai namespace, restart simulator và worker retry/lease recovery.
- Frontend: `npm run lint` và production `npm run build` thành công.
- Playwright: **1 E2E passed**, kiểm tra đăng nhập, kịch bản đất khô, dự báo mưa,
  upload lá/quả, hộp Mock YOLO, lịch sử sau reload và viewport mobile 390px không tràn ngang.
- Alembic: database ở head, `alembic check` không phát hiện lệch schema.
- Compose: cấu hình hợp lệ, PostgreSQL/MinIO/Mosquitto healthy, bucket init exit 0.
- Script Windows: chạy → dừng đúng cây process → chạy lại thành công; chỉ báo sẵn sàng
  khi MQTT kết nối, thiết bị online và đã có telemetry mới.
- Kiểm tra ảnh chụp desktop/mobile tại `.runtime/`; artifact kiểm thử được Git ignore.

Các bản ghi simulation/ảnh fixture tạo khi kiểm thử vẫn có thể xuất hiện trong lịch sử.
Đây là kết quả lịch sử của bản Mock, không mô tả runtime AI hiện tại.
Kiểm chứng tích hợp model được ghi tại [ai-integration.md](ai-integration.md).
