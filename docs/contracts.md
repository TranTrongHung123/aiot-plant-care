# Hợp đồng dữ liệu dự kiến — v1

Đây là thiết kế mục tiêu để thống nhất backend, firmware và simulator.

**Cập nhật phiên bản demo:** contract dưới đây là mục tiêu cho phần cứng thật.
Backend/simulator hiện đã thực thi phần simulation; danh sách API và schema thực tế
ở [demo.md](demo.md). Demo dùng một device ID cố định, readings hợp lệ dạng flat,
soil_moisture_pct nullable; chưa có credential camera hoặc per-reading quality field.
Ảnh hiện được upload từ dashboard có Bearer token, thời điểm upload do server ghi nhận.

## MQTT

Prefix: `plantcare/v1/{live|simulation}/{device_id}`. Backend chỉ subscribe namespace
của chế độ đang chạy. Credentials thiết bị live phải có ACL theo device/topic;
credentials simulator không được truy cập namespace live.

| Topic suffix | Chiều            | QoS | Retain | Nội dung                         |
| ------------ | ---------------- | --- | ------ | -------------------------------- |
| `telemetry`  | device → backend | 1   | false  | Các phép đo                      |
| `status`     | device → backend | 1   | true   | Online/offline; dùng LWT offline |
| `command`    | backend → device | 1   | false  | Yêu cầu điều khiển có hạn dùng   |
| `ack`        | device → backend | 1   | false  | Kết quả xử lý command            |

Envelope telemetry: `schema_version=1`, `message_id`, `device_id`, `source`,
`measured_at` (UTC ISO 8601), `boot_id`, `sequence`, `readings`.
Backend thêm `received_at`; unique `(device_id, message_id)` để chống ghi trùng.
Device ID và source phải khớp topic và danh tính đã xác thực.

Readings: `temperature_c`, `humidity_pct`, `soil_moisture_raw`,
`soil_moisture_pct` (nullable khi chưa hiệu chuẩn), `pressure_hpa`.
Mỗi giá trị có trạng thái valid/invalid/uncalibrated; thiếu dữ liệu dùng null,
không biến lỗi thành 0. Không dùng dữ liệu retained/cũ để tự khởi động tưới.

Command: `schema_version`, `command_id`, `device_id`, `source`, `issued_at`,
`expires_at`, `action` (`pump_on`/`pump_off`), `duration_ms` cho bật bơm.
Firmware giới hạn duration tại chỗ, từ chối lệnh hết hạn và bật bơm khi chưa đồng bộ
thời gian. Xử lý lại cùng command ID không kéo dài thời gian chạy; lưu dấu lệnh
qua reconnect/reboot trong thời hạn có thể phát lại. Retry dùng lại ID và hạn cũ.
Lệnh tắt bơm hợp lệ có ưu tiên cao hơn mọi lịch tưới.

ACK: `command_id`, `device_id`, `source`, `timestamp`, `status`
(`accepted`, `rejected`, `completed`, `failed`) và `reason`.
Backend lưu vòng đời requested → published → accepted → completed, có nhánh
rejected/failed/timed_out; ACK đến muộn được lưu để đối soát, không suy diễn bơm OFF
chỉ vì timeout. PUBACK của broker chỉ xác nhận truyền bản tin.

## HTTP, ảnh và realtime

- REST dự kiến `/api/v1`; camera upload multipart có device ID, image ID, captured_at,
  source và purpose (`leaf`/`fruit`), xác thực credential riêng từng thiết bị.
- Kiểm tra nội dung ảnh thực, kích thước tối đa và MIME; giới hạn khởi điểm 5 MiB,
  phải kiểm chứng với camera. Không tin tên file do client gửi.
- Bucket `plant-images`: key `{source}/{device_id}/{yyyy}/{mm}/{image_id}.jpg`;
  bucket `ai-artifacts`: ảnh kết quả/artifact. Các bucket đều private.
- Backend lưu object trước, sau đó transaction metadata + inference job. Nếu DB lỗi,
  thử xóa object và ghi log để đối soát object mồ côi. Không giả định DB và S3 có
  transaction chung. Trả nhận việc, không giữ request đợi inference nặng.
- Worker xử lý job theo image ID, task và model version; có retry giới hạn,
  timeout, error và khóa/lease nhận job để phục hồi sau crash.
- WebSocket `/ws` có xác thực, source, event ID và thời điểm; khi reconnect lấy lại
  trạng thái từ REST, không coi WebSocket là nguồn lịch sử duy nhất.

## PostgreSQL: thực thể dự kiến

| Nhóm         | Thực thể                                                    |
| ------------ | ----------------------------------------------------------- |
| Thiết bị/cây | plants, devices, device_assignments                         |
| Quan trắc    | sensor_readings, weather_observations                       |
| Điều khiển   | irrigation_policies, irrigation_commands, irrigation_events |
| Ảnh/AI       | images, inference_jobs, predictions, model_versions         |
| Truy cập     | users hoặc cơ chế quản trị đơn giản, audit_events           |

Dùng UUID, timestamptz UTC, khóa ngoại và index `(device_id, measured_at)`.
Lưu source ở dữ liệu liên quan. Metadata linh hoạt dùng JSONB nhưng các trường dùng
để lọc/index phải có cột rõ ràng. Dùng Alembic khi triển khai; chưa tạo bảng ở bước scaffold.
Job AI dùng PostgreSQL trước để không cần Redis. Một API process + một worker AI là
mục tiêu demo; API giữ một MQTT consumer/scheduler duy nhất, chưa scale nhiều Uvicorn workers.

## Inference đã tích hợp (15/10/2026)

Job mới dùng `provider=trained`, version artifact cụ thể; kết quả `is_mock=false`,
`predicted_at`, `source`/`input_source` và confidence. Job Mock cũ giữ nguyên nhãn.
OpenWeather lưu `measured_at`/`received_at` UTC riêng trong `weather_observations`.
Mưa dùng snapshot nhân quả 6 giờ, không dùng telemetry simulation; API trả 409 khi
thiếu dữ liệu. Contract chi tiết và giới hạn: [ai-integration.md](ai-integration.md).

## Thời tiết và lịch tưới đã tích hợp (16/10/2026)

`PUT /api/v1/weather/mode` lưu `mode=demo|live` và `demo_rain`. DEMO chỉ dùng với
thiết bị simulation, không tạo kết quả AI hoặc quan trắc OpenWeather giả.
Dashboard trả `weather_control` gồm nguồn, trạng thái sẵn sàng, quyết định mưa và
lý do; xác suất/model version để null trong DEMO. LIVE chỉ nhận dự báo trained
đúng địa điểm, đúng version và còn hạn 60 phút.

`PUT /api/v1/irrigation/policy` bổ sung `schedule_kind=daily|interval`, `daily_times` (HH:MM,
Asia/Ho_Chi_Minh) và `interval_minutes` (1–1440). `enabled=false` là thủ công;
bật tự động vẫn cho phép lệnh thủ công với kiểm tra an toàn. Lịch lặp lưu UTC và
trạng thái lượt gần nhất để tránh thực thi lại; lệnh tự động lưu `weather_context`
và `schedule_at`. Mỗi lượt kiểm tra lại đất, thời tiết, MQTT, trạng thái bơm,
ACK, dữ liệu cảm biến và cooldown trước khi gửi. Chi tiết và migration tại
[weather-modes.md](weather-modes.md).
