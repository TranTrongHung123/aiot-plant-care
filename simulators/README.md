# Simulator scaffold

- `esp32/`: telemetry, status/LWT, nhận command và phản hồi ACK theo contract.
- `camera/`: gửi ảnh mẫu qua cùng luồng upload với camera thật, source simulation.
- Dùng namespace `plantcare/v1/simulation`, device ID riêng, không dùng credential live.
- Kịch bản cần có khi triển khai: lệnh trùng, lệnh hết hạn, mất kết nối, cảm biến cũ,
  camera lỗi và ACK chậm. Không thay simulator bằng dữ liệu giả ngay trong dashboard.

Simulator MQTT đã triển khai tại `backend/app/workers/simulator.py`, dùng chung
contract/settings với backend. Chạy `uv run python -m app.workers.simulator` từ
`backend/` hoặc dùng script demo. Chức năng camera mô phỏng hiện qua upload ảnh trên
dashboard; chưa có tiến trình camera riêng. Xem `docs/demo.md`.
