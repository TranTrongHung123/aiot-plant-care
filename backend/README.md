# Backend — End-to-End với model đã huấn luyện

Xem [hướng dẫn demo](../docs/demo.md) để chạy toàn hệ thống trên Windows.

Python 3.12, uv, FastAPI, SQLAlchemy/Alembic, psycopg, Paho MQTT và MinIO SDK.
Chạy `uv sync --locked` trong thư mục này để tạo `.venv` từ `uv.lock`.

Sau `uv run alembic upgrade head`, chạy từng process trong terminal riêng:

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
uv run python -m app.workers.inference
uv run python -m app.workers.simulator
```

API có Bearer auth qua `APP_TOKEN`; WebSocket nhận token ở frame đầu. Runtime chỉ
cho phép thiết bị simulation + AI thật trên CPU. PostgreSQL/MinIO/MQTT phải chạy trước.

| Thư mục | Trách nhiệm |
| --- | --- |
| `app/api/` | REST `/api/v1`, WebSocket, validation/auth ở biên |
| `app/core/` | Settings, lifespan, logging, auth |
| `app/db/` | Engine, session và ORM models |
| `app/modules/` | Devices, telemetry, irrigation, images, weather, inference |
| `app/integrations/` | MQTT local demo, S3/MinIO, kiểm tra ảnh |
| `app/workers/` | Worker AI dùng chung codebase; không chặn event loop |
| `migrations/` | Alembic versioned schema |
| `tests/` | Unit tests và E2E opt-in |

Settings đọc `.env` tại gốc repo bằng đường dẫn rõ ràng. Dùng
`MINIO_APP_USER/PASSWORD`, không dùng root MinIO.
DB user hiện tại là owner của DB phát triển; cần tách migration/runtime role nếu triển khai ra ngoài laptop.
Runtime CPU được khóa trong `uv.lock`; chỉ worker import Torch/YOLO/XGBoost.
Cấu hình artifact, OpenWeather và giới hạn: [tích hợp AI](../docs/ai-integration.md).
