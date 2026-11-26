# Trợ lý cây trồng và benchmark Phase 1

Mở **Trợ lý cây trồng** trong dashboard đã đăng nhập. Trợ lý đọc số đo, trạng thái thiết bị, lịch tưới, dự báo XGBoost còn hạn và kết quả ảnh đã xử lý. Yêu cầu bật/tắt bơm chỉ hiển thị xem trước. Chat dùng một không gian chung cho những người có `APP_TOKEN`.

## Cấu hình

Thêm vào file `.env` ở thư mục gốc.

```dotenv
CHAT_ENABLED=true
CHAT_DEFAULT_MODE=cloud
CHAT_CLOUD_MODEL=gpt-5.4-nano
CHAT_CLOUD_API_KEY=
```

Điền key OpenAI trực tiếp trong `.env`. Sau đó, nâng cấp cơ sở dữ liệu và khởi động lại FastAPI.

```bash
cd backend
uv run --locked alembic upgrade head
uv run --locked uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Chạy frontend theo [README](../README.md). Để dùng Local, chạy Ollama trên máy backend và tải model.

```bash
ollama pull qwen3.5:4b-q4_K_M
```

`CHAT_LOCAL_BASE_URL` mặc định là `http://127.0.0.1:11434`, trỏ đến gốc API Ollama. `CHAT_LOCAL_MODEL` mặc định là `qwen3.5:4b-q4_K_M`. Model cần trả được JSON theo schema. Cloud và Local giữ nhà cung cấp đã chọn. Tự động ưu tiên `CHAT_AUTO_PREFERENCE` và có thể thử nhà cung cấp còn lại một lần khi mất kết nối, hết thời gian hoặc bị giới hạn lượt gọi. Giao diện báo nhà cung cấp thực tế.

Ở máy đang dùng Docker cho Plant Care, container `aiot-plant-care-ollama` mở API trên `127.0.0.1:11435`. File `.env` của máy trỏ `CHAT_LOCAL_BASE_URL` đến địa chỉ này để dành cổng 11434 cho `ollama-diabetes`. Model được lưu trong volume `aiot-plant-care-ollama-models`. Sau khi khởi động lại máy, chạy `docker start aiot-plant-care-ollama` để dùng lại model đã tải.

Một lần gọi LLM chọn ý định và tham số giới hạn cho mỗi câu hỏi. Backend dựng câu trả lời từ dữ liệu cấu trúc và lưu nguồn cùng lịch sử chat. Cloud nhận câu hỏi và tối đa bốn câu hỏi trước. Số đo, nội dung kết quả AI và ảnh từ hệ thống được xử lý tại backend. Bộ lọc che các secret mà ứng dụng nhận biết. Hãy tránh nhập thông tin nhạy cảm mới vào chat. Giới hạn mặc định là 12 lượt/phút, 2 lượt đồng thời và 256 token đầu ra cho bộ định tuyến.

## Chạy benchmark

Chạy từ `backend/`. Bộ đánh giá dùng 29 câu tiếng Việt và dữ liệu tổng hợp cố định. Nó chạy cùng bộ công cụ tạo câu trả lời của ứng dụng.

```bash
uv run --locked python -m benchmarks.chat_benchmark --require-pass
uv run --locked python -m benchmarks.chat_benchmark --modes cloud --case sensor-fresh
uv run --locked python -m benchmarks.chat_benchmark --modes cloud local --output ../.runtime/chat-benchmark.json
```

- **Fixtures** dùng ý định đáp án mẫu để kiểm tra câu trả lời, nguồn và điều kiện đọc. Kết quả này đo phần Python.
- **Cloud và Local** gọi model thật trên cùng 29 câu, mọi câu hỏi và dữ liệu đều là fixture. Cloud tính phí theo token. Dùng `--repeat 3` nếu cần đo độ ổn định nhiều lần.
- Báo cáo có độ đúng chọn ý định và tham số, số câu trả lời đạt kiểm tra dữ liệu, các ca an toàn, lỗi provider, độ trễ p50/p95, token, model và fallback. Độ trễ đo trên các câu có phản hồi. Đọc chi tiết từng case trong JSON để phân biệt lỗi phân loại với lỗi dữ liệu.
- Dùng `--require-pass` để trả mã lỗi khi có ca sai hoặc chế độ chưa cấu hình. Kiểm thử mất dịch vụ và fallback xác định nằm trong `backend/tests/test_chat.py`.

Khi so sánh, xem trước nhóm an toàn như yêu cầu bật bơm và đòi thông tin bí mật. Sau đó so độ đúng các nhóm cảm biến, tưới, thời tiết, ảnh và độ trễ. Benchmark đo đối chiếu với đáp án mẫu. Để thử trải nghiệm thực tế, chạy thêm Local khi worker AI và livestream đang hoạt động trên máy.

## Kiểm tra trước khi nghiệm thu

Chạy `uv run --locked pytest -q` trong `backend/` và `npm run test:e2e -- tests/chat.spec.ts` trong `frontend/` khi frontend đang chạy.

Kiểm tra Auto từ `backend/` bằng cấu hình chỉ áp dụng cho từng lệnh. Lệnh đầu mô phỏng Ollama mất kết nối. Lệnh sau mô phỏng Cloud thiếu key. Báo cáo JSON phải ghi đúng `actual_provider` và `fallback_used`.

```bash
CHAT_AUTO_PREFERENCE=local CHAT_LOCAL_BASE_URL=http://127.0.0.1:1 uv run --locked python -m benchmarks.chat_benchmark --modes auto --case sensor-fresh --output ../.runtime/chat-auto-local-down.json
CHAT_AUTO_PREFERENCE=cloud CHAT_CLOUD_API_KEY= uv run --locked python -m benchmarks.chat_benchmark --modes auto --case sensor-fresh --output ../.runtime/chat-auto-cloud-unconfigured.json
```
