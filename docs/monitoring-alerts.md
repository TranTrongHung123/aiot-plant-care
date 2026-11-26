# Giám sát và cảnh báo Phase 2

Dashboard hiển thị cảnh báo mới ở **Cần chú ý**. Mở **Cảnh báo** để xem sự cố đang diễn ra, cảnh báo chưa xem hoặc toàn bộ lịch sử. **Đánh dấu đã xem** ghi lại thời điểm xem lần đầu. Dấu **Đã phục hồi** xuất hiện khi backend nhận đủ bằng chứng phục hồi.

## Cách bật

Chạy từ `backend/` rồi khởi động lại tiến trình FastAPI.

```bash
uv run --locked alembic upgrade head
```

Migration `f94001` thêm bảng cảnh báo và vị trí đọc sự kiện. Lần đầu bộ giám sát chạy, lịch sử lệnh tưới và ảnh cũ được lấy làm mốc. Cảnh báo đã lưu và vị trí đọc sự kiện được giữ qua các lần khởi động tiếp theo.

## Ngưỡng mặc định

| Cấu hình | Giá trị |
| --- | --- |
| `MONITORING_ENABLED` | `false`, đặt `true` trong `.env` để bật cảnh báo |
| `MONITORING_INTERVAL_SECONDS` | 5 giây |
| `MONITORING_OFFLINE_SECONDS` | 45 giây không có trạng thái thiết bị |
| `MONITORING_STALE_SECONDS` | 30 giây không có số đo mới khi thiết bị còn online |
| `MONITORING_DRY_SECONDS` | 120 giây dưới ngưỡng tưới |
| `MONITORING_COOLDOWN_SECONDS` | 60 giây trước khi mở lại một cảnh báo đã phục hồi |
| `MONITORING_LEAF_SCORE` | 0,80 |
| `MONITORING_LEAF_COOLDOWN_MINUTES` | 15 phút cho ảnh cùng nhãn |

Đất khô cần chuỗi số đo hợp lệ. Hai mẫu liên tiếp đạt ngưỡng tưới cộng 3 điểm phần trăm là bằng chứng phục hồi. Cảnh báo lỗi tưới ghi trạng thái ACK. Cảnh báo ảnh hiển thị điểm MobileNetV3 và đề nghị xem lại ảnh.

## Dashboard và trợ lý

- `GET /api/v1/alerts` hỗ trợ `attention`, `unread`, `all` và phân trang bằng con trỏ. `POST /api/v1/alerts/{id}/acknowledge` đánh dấu đã xem. Các tuyến dùng Bearer token trong header.
- Backend chạy quy tắc Python theo lịch và lưu kết quả vào PostgreSQL. Dashboard đọc các kết quả này khi mở trang và định kỳ khoảng 10 giây.
- GPT‑5.4 nano định tuyến khi người dùng gửi câu hỏi trong chat. Trợ lý trả lời từ dữ liệu cảnh báo đã lưu, ví dụ “Vườn đang có cảnh báo gì?” hoặc “ESP32 mất kết nối từ lúc nào?”.
- ESP32 thật và simulator dùng chung `sim-esp32-01`. Khi thử với thiết bị thật, dùng một nguồn phát tại một thời điểm để lịch sử giám sát phản ánh đúng thiết bị.

## Kiểm thử

- Chạy `uv run --locked pytest -q` trong `backend/`. Đặt `RUN_MONITORING_POSTGRES=1` để chạy thêm kiểm thử lưu trữ và API trong schema PostgreSQL tạm.
- Chạy `npm run test:e2e -- tests/alerts.spec.ts` trong `frontend/` khi Next.js đã chạy. Test dùng phản hồi API mẫu để kiểm tra đọc, đánh dấu đã xem, phục hồi và cache.
- Chạy `uv run --locked python -m benchmarks.chat_benchmark --modes fixtures` để kiểm tra dữ liệu và `--modes cloud` để đo định tuyến với Nano. Bộ ca gồm 29 câu Phase 1 và 7 câu về giám sát.
- Dùng broker mô phỏng riêng khi chạy các ca tích hợp có đổi kịch bản hoặc gửi lệnh tưới. Firmware thật và simulator nhận cùng định danh MQTT trong cấu hình demo.

## Kết quả nghiệm thu

- Backend đạt 49 ca trong bộ kiểm thử chính, gồm các ca PostgreSQL, và 5 ca tích hợp trên môi trường riêng. Migration `f94001` đã được kiểm tra trên database mới và khi nâng cấp từ Phase 1, rồi áp dụng vào database dự án.
- Playwright đạt 11 ca hồi quy. Một ca bổ sung kiểm tra cảnh báo đã xem và đã phục hồi vẫn hiển thị đúng sau khi tải lại với API và database thật của môi trường mô phỏng.
- Benchmark GPT‑5.4 nano đạt 36/36 ca, gồm 6/6 ca an toàn. Độ trễ p50 là 909 ms và p95 là 1.082 ms.
- Demo dùng chu kỳ kiểm tra 2 giây, ngưỡng đất khô 20 giây và dữ liệu mẫu cho lỗi ACK và ảnh lá. Các cảnh báo giữ nguyên mã qua lần khởi động lại backend và vẫn hiển thị khi Cloud không khả dụng.
