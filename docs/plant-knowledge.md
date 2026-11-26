# Tra cứu tài liệu cà chua Phase 3

Trợ lý cây trồng có thể tìm trong 9 tài liệu về cà chua và 2 tài liệu vận hành của dự án. GPT‑5.4 nano chọn công cụ khi nhận câu hỏi. Backend tra các đoạn tiếng Việt đã biên soạn ngay trong bản ứng dụng rồi ghép với dữ liệu vườn. Cảnh báo dashboard được tạo theo lịch như mô tả trong [hướng dẫn Phase 2](monitoring-alerts.md).

## Dùng trong chat

- “Ảnh lá mới nhất gợi ý bệnh gì và tôi nên kiểm tra dấu hiệu nào?” lấy nhãn và điểm từ ảnh đã xử lý, rồi tra theo mã nhãn MobileNetV3.
- “Ảnh mới nhất có mấy quả chín và theo tài liệu thì khi nào nên hái?” lấy số lượng từ YOLO và hướng dẫn thu hoạch từ tài liệu cho cà chua trồng chậu.
- “Dashboard báo đất khô. Số đo độ ẩm đất hiện tại là bao nhiêu và nên kiểm tra giá thể thế nào?” lấy cảnh báo đã lưu, số đo mới nhất và hướng dẫn kiểm tra chậu.

Mở **Nguồn dữ liệu** dưới câu trả lời để xem bằng chứng và các mục tài liệu được đánh số. Số đo, kết quả ảnh và cảnh báo có thời điểm riêng. Nguồn công khai có link gốc, tên mục và phiên bản. Thông tin này được lưu cùng tin nhắn.

Trong cùng hội thoại, câu hỏi nối tiếp như “Lá đó nên nhìn mặt nào?” có thể dùng lại ảnh và nguồn tham khảo từ bốn lượt gần nhất. Hỏi “ảnh mới nhất” để lấy kết quả hiện tại. Khi bạn yêu cầu so sánh hai bệnh có tên cụ thể, trợ lý tra riêng tài liệu của cả hai bệnh.

Câu hỏi “Tôi đã kiểm tra, nếu là mốc sương thì xử lý thế nào?” tra phần phòng chống của tài liệu Khuyến nông Việt Nam. Khi kho chỉ có phần nhận biết cho một bệnh, yêu cầu xử lý sẽ báo thiếu hướng dẫn phù hợp.

## Cập nhật kho tài liệu

Kho hiện nằm ở [`plant_knowledge_v1.json`](../backend/app/data/plant_knowledge_v1.json). Chỉnh nội dung tiếng Việt theo từng mục nguồn, ghi phạm vi canh tác, mã đoạn và mã nhãn tương ứng khi có. Kiểm tra lại link và mức độ phù hợp của đoạn trước khi cập nhật phiên bản corpus. Tra cứu dùng các bí danh có dấu, không dấu và tên chuyên môn để tìm trong kho cục bộ. Các lần đọc lại hội thoại dùng phiên bản trích dẫn đã lưu ở thời điểm trả lời.

Khởi động lại FastAPI để nạp phiên bản mới. Dùng database và cấu hình `CHAT_CLOUD_*` đã có.

## Kiểm thử

Chạy từ `backend/`.

```bash
uv run --locked pytest -q tests/test_chat.py tests/test_knowledge.py
uv run --locked python -m benchmarks.chat_benchmark --modes fixtures --require-pass
uv run --locked python -m benchmarks.chat_benchmark --modes cloud --output ../.runtime/chat-benchmark-phase3-nano.json
```

Benchmark dùng dữ liệu vườn mô phỏng để đối chiếu định tuyến, bằng chứng, số lần dẫn đúng nguồn, độ trễ và token. Bộ ca gồm 36 ca hồi quy Phase 1–2 và 24 ca Phase 3.

## Kết quả nghiệm thu

- Fixture và GPT‑5.4 nano cùng đạt 60/60 ca, gồm 11/11 ca an toàn. Có 17/17 ca tìm đúng tài liệu, mục và phiên bản, với 0 trích dẫn sai. Nano đạt p50 khoảng 1,03 giây và p95 khoảng 1,38 giây.
- Bộ backend đạt 62 ca có kiểm tra PostgreSQL và 5 ca tích hợp trên broker mô phỏng. Playwright đạt 11 ca, gồm kiểm tra dẫn nguồn trên màn hình nhỏ và sau khi tải lại. Bản build frontend đã được kiểm tra.
- Demo qua FastAPI và PostgreSQL dùng kết quả ảnh mẫu cho hai ca lá và quả, và dùng cảnh báo đất khô do broker mô phỏng tạo cho ca thứ ba. Nano chọn đúng công cụ, ba câu trả lời lưu được dẫn nguồn và trạng thái đọc lại. Cảnh báo đất khô được tạo khi Cloud trả lỗi. Các lượt demo không tạo lệnh tưới.
