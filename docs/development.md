# Khởi tạo trên Windows

Để chạy toàn bộ backend/frontend End-to-End, dùng [demo.md](demo.md).
Tài liệu này tập trung vào hạ tầng và cấu hình trên Windows.

## Phạm vi hiện tại

- Docker Compose: PostgreSQL 17, MinIO build từ source, init bucket/account và
  Mosquitto tùy chọn. Dữ liệu trong named volumes, cổng chỉ bind `127.0.0.1`.
- Backend: Python 3.12 + uv, FastAPI, Alembic, MQTT, worker AI thật (CPU) và simulator.
- Frontend: Next.js dashboard kết nối API/WebSocket; firmware thật chưa tích hợp.
- Ba AI đều là phạm vi chính; thứ tự triển khai không loại bỏ bài toán nào.

## Lần đầu

Cài Docker Desktop (Linux containers/WSL2), uv và Node.js phù hợp frontend hiện có.
Chạy từ thư mục gốc trong PowerShell:

```powershell
# Chỉ copy nếu .env chưa tồn tại; thay các change-me bằng mật khẩu riêng.
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose config --quiet
docker compose build minio
docker compose up -d
docker compose ps -a
docker compose logs minio-init
```

Trong phiên khởi tạo bằng agent, `.env` được tạo với mật khẩu ngẫu nhiên nếu chưa có;
đọc tại máy, không đưa vào Git. `.env.example` chỉ là mẫu, không dùng mật khẩu mẫu cho demo chia sẻ.

MinIO build lần đầu tải Go/dependency và có thể mất vài phút, cần Internet.
`minio-init` phải kết thúc với exit code 0; PostgreSQL và MinIO phải healthy.
Init chạy lại an toàn với cùng cấu hình, tạo hai bucket private và tài khoản ứng dụng
chỉ có quyền trên hai bucket. Đổi tên bucket phải cập nhật init script, policy và env đồng bộ.

| Dịch vụ               | Địa chỉ trên laptop                    | Tài khoản                             |
| --------------------- | -------------------------------------- | ------------------------------------- |
| PostgreSQL            | `127.0.0.1:5432`, database `plantcare` | `POSTGRES_USER/PASSWORD`              |
| MinIO S3              | `http://127.0.0.1:9000`                | `MINIO_APP_USER/PASSWORD` cho backend |
| MinIO Console         | `http://127.0.0.1:9001`                | `MINIO_ROOT_USER/PASSWORD`            |
| MQTT local (tùy chọn) | `127.0.0.1:1883`                       | Anonymous, chỉ simulator trên máy     |

Console của bản Community có thể không cung cấp UI quản trị đầy đủ; dùng `mc`
trong image khi cần. Không đưa khóa S3/DB vào biến `NEXT_PUBLIC_*`.
Trong container, hostname là `postgres`, `minio`, `mqtt`; trên host dùng localhost.
Browser không phân giải được hostname Compose. ESP32 cũng không truy cập localhost của laptop;
HiveMQ Cloud vẫn là đường MQTT chính cho phần cứng.

## Backend và frontend

```powershell
cd backend
uv sync --locked
cd ../frontend
npm ci
# Chạy frontend khi phát triển:
npm run dev
```

Frontend dùng mẫu `frontend/.env.example`, copy thành `.env.local` khi bắt đầu tích hợp.
Backend entrypoint, migration, worker và simulator được hướng dẫn tại [demo.md](demo.md).
Runtime inference CPU đã được khóa trong backend; cấu hình artifact/OpenWeather tại
[ai-integration.md](ai-integration.md). Không huấn luyện trên laptop.

## Huấn luyện AI trên Colab

Cả ba pipeline train trên hosted Google Colab. Laptop chạy demo và đóng ZIP mã nguồn
bằng `powershell -NoProfile -File scripts/package-colab.ps1`, không cài CUDA/training local.
Backend giữ `backend/.venv` riêng; xem [hướng dẫn ML](../ml/README.md) về upload Drive,
chọn GPU cho MobileNetV3/YOLO và CPU Colab cho XGBoost, lưu checkpoint và resume.

## MQTT Cloud và local

HiveMQ Cloud: điền `MQTT_HOST`, credentials, giữ port 8883 và `MQTT_TLS=true`;
xác minh chứng chỉ TLS. Cấu hình quyền publish/subscribe theo contract từng thiết bị.
Chưa có credentials nên bước bootstrap không kiểm thử kết nối HiveMQ Cloud.

Local là tùy chọn, không tự chuyển từ cloud khi lỗi:

```powershell
docker compose --profile local-mqtt up -d mqtt
```

Demo hiện tại dùng `DEMO_MQTT_HOST=127.0.0.1`, `DEMO_MQTT_PORT=1883` riêng biệt;
giữ `DEVICE_MODE=simulation`. `MQTT_HOST/PORT/TLS` dành cho bước HiveMQ Cloud sau.
Broker anonymous này không mở ra LAN/Internet.
Khi kết nối camera thật cần thiết lập endpoint API LAN, firewall và xác thực ở bước triển khai.

## Vận hành

```powershell
docker compose --profile local-mqtt ps -a
docker compose logs --tail 100 postgres minio minio-init
docker compose --profile local-mqtt stop
docker compose --profile local-mqtt start
# Dừng và bỏ container nhưng giữ named volumes:
docker compose --profile local-mqtt down
```

Không dùng `down -v` trừ khi chủ động muốn xóa toàn bộ dữ liệu. Đổi POSTGRES_PASSWORD
trong `.env` không đổi password của database đã tồn tại; cần đổi role bằng SQL có kiểm soát.
Khi đổi MinIO app password, chạy lại `docker compose run --rm minio-init`;
đổi root password cần recreate service MinIO trước. Không xóa volume để sửa lỗi đăng nhập.
Nếu cổng bị chiếm, đổi biến port rồi cập nhật endpoint của client tương ứng.

## Quyết định về MinIO

Tại ngày 13/10/2026, [repository MinIO](https://github.com/minio/minio) thông báo
không còn được duy trì và Community phân phối source-only. Dự án giữ MinIO để phục vụ
bài tập local, build source tag `RELEASE.2025-10-15T17-29-55Z` và mc tag
`RELEASE.2025-08-13T08-35-41Z`, không phụ thuộc `minio/minio:latest`.
Đây không phải lựa chọn mặc định cho triển khai public lâu dài; adapter S3 cho phép thay
storage mà không đổi nghiệp vụ. Image nền PostgreSQL/Mosquitto được ghim nhánh major;
trước buổi demo nên lưu image digest đã kiểm chứng để tái lập chính xác.

Tham khảo: [Compose health/dependency](https://docs.docker.com/compose/how-tos/startup-order/),
[MinIO source](https://github.com/minio/minio), [mc releases](https://github.com/minio/mc/releases),
[HiveMQ Cloud TLS](https://community.hivemq.com/t/use-1883-port-instead-of-8883/2010).

## Kiểm chứng phiên khởi tạo 13/10/2026

- Compose config hợp lệ với profile local-mqtt; image MinIO build thành công.
- PostgreSQL, MinIO và MQTT local đều healthy; minio-init exit 0, chạy lại exit 0.
- Kết nối PostgreSQL bằng mật khẩu ứng dụng và truy vấn database thành công.
- Tài khoản MinIO app đọc/ghi/xóa object thử trên cả hai bucket thành công;
  truy cập không xác thực bị từ chối HTTP 403. Object thử đã được xóa.
- MQTT local publish/subscribe QoS 1 round-trip thành công trên topic simulation.
- `uv sync --locked` thành công. Không có test nghiệp vụ để chạy ở giai đoạn này.
- Chưa kiểm thử HiveMQ Cloud, frontend tích hợp, camera, bơm hoặc mô hình AI.
