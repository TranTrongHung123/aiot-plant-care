# Kịch bản Thuyết trình & Demo Dự án: AIoT Plant Care

---

## 1. Giới thiệu & Phạm vi đề tài

- **Tên đề tài**: Hệ thống giám sát và chăm sóc cây cà chua thông minh theo mô hình **AIoT** (*AIoT Plant Care*).
- **Phạm vi**:
  - Áp dụng trên mô hình **chậu cây cà chua thực nghiệm**, kết hợp mạch phần cứng IoT thật (**ESP32 + ESP32-CAM**) với hệ thống Backend và 3 mô hình AI đã huấn luyện.
  - Hệ thống giám sát liên tục các chỉ số môi trường và hình ảnh thực tế của cây, từ đó tự động đưa ra quyết định tưới nước thông minh, tiết kiệm và an toàn.
- **Lý do chọn cây cà chua**:
  - Cà chua là cây trồng rất cần độ ẩm ổn định và duy trì liên tục: thiếu nước cây nhanh héo, rụng hoa, nhưng tưới quá đẫm hoặc tưới ngay trước khi trời mưa dễ gây **úng rễ, nứt quả chín** và làm phát sinh nấm bệnh trên lá.
  - Việc ứng dụng hệ thống AIoT giúp cây cà chua được cấp nước đúng lúc, sinh trưởng ổn định và cho năng suất thu hoạch tốt.

---

## 2. Luồng hoạt động chính của hệ thống

1. **Luồng cảm biến & điều khiển bơm (qua giao thức MQTT)**:
   ```text
   [Cảm biến DHT22, BMP180, Độ ẩm đất] -> [ESP32] --(MQTT mỗi 5s)--> [FastAPI Backend & PostgreSQL]
   [ Model AI dự báo mưa XGBoost ] + [ Bộ quy tắc quyết định & Chốt an toàn ]
          --(Lệnh tưới MQTT)--> [ESP32 đóng Relay bật bơm & gửi ACK phản hồi]
   ```

2. **Luồng hình ảnh & phân tích AI (qua HTTP & WebSocket)**:
   ```text
   [ESP32-CAM OV2640] --(HTTP POST / MJPEG Stream)--> [FastAPI & MinIO]
          --> [AI Worker: MobileNetV3 (Bệnh lá) & YOLO (Đếm quả xanh/chín)]
   ```

3. **Giao diện hiển thị (Frontend Next.js)**:
   - Nhận dữ liệu thời gian thực qua WebSocket, hiển thị thông số môi trường, biểu đồ, điều khiển tưới và kết quả phân tích AI.

---

## 3. Giải thích các trang chức năng chính

1. **Trang Tổng quan (`/`)**: Hiển thị các chỉ số môi trường thời gian thực của cây (nhiệt độ, độ ẩm không khí, độ ẩm đất, áp suất khí quyển) và trạng thái thiết bị.
2. **Trang Lịch tưới (`/schedule`)**: Cấu hình lịch tưới tự động cho cây (theo khung giờ hoặc chu kỳ lặp, ngưỡng độ ẩm đất, thời lượng tưới) kết hợp dự báo thời tiết.
3. **Trang Điều khiển tưới (`/control`)**: Điều khiển bật/tắt máy bơm thủ công và theo dõi diễn biến độ ẩm đất.
4. **Trang Phân tích AI (`/ai`)**: Xem livestream trực tiếp từ ESP32-CAM, ghi nhận ảnh định kỳ hoặc chụp ảnh cây để theo dõi số lượng quả, độ chín (**YOLO**) và phân tích bệnh trên lá (**MobileNetV3**).

---

## 4. Kịch bản Demo trực tiếp (4–5 phút)

> **Thiết lập khi Demo**: Trong điều kiện thực tế, cứ định kỳ (ví dụ mỗi 6 tiếng hoặc theo khung giờ trong ngày) hệ thống sẽ kiểm tra các chỉ số của vườn cây 1 lần. Khi demo trực tiếp cho thầy xem, nhóm đặt **Chu kỳ kiểm tra lặp lại mỗi `1 phút`** (Ngưỡng độ ẩm đất `30%`, thời lượng tưới `3–5 giây`, thời gian nghỉ `30 giây`) để quan sát ngay toàn bộ các trường hợp.

### Bước 1: Demo giám sát cảm biến thật & Tưới thủ công (`/control`)
- Cho thầy xem các chỉ số nhiệt độ (**DHT22**), áp suất (**BMP180**) và độ ẩm đất từ mạch ESP32 đang truyền về trực tiếp mỗi 5 giây.
- Bấm **`Tưới ngay`** (`3 giây`) để kiểm tra phần cứng: Relay đóng bật máy bơm thật, chạy đủ 3 giây rồi tự ngắt và báo `Hoàn tất` về Web.

### Bước 2: Demo Tưới tự động & Kịch bản Hoãn mưa – Tưới bù (`/schedule`)
Bật **Tự động theo lịch (chu kỳ `1 phút`, ngưỡng đất `30%`)** và demo lần lượt 3 tình huống:

1. **Tình huống 1 — Điều kiện bình thường (Đất khô + Dự báo không mưa $\rightarrow$ Tưới ngay)**:
   - Cảm biến đất đang báo khô (`< 30%`) và dự báo thời tiết là **`Không mưa`** (hoặc tab **`LIVE`** model XGBoost dự báo *Ít mưa*).
   - Khi đến mốc kiểm tra: Hệ thống nhận thấy đất khô và không mưa nên **tự động bật bơm tưới cây luôn**.
2. **Tình huống 2 — Đất đã đủ độ ẩm (`>= 30%`) $\rightarrow$ Bỏ qua**:
   - Nhúng đầu dò cảm biến đất vào nước/đất ẩm (`> 30%`).
   - Khi đến mốc kiểm tra: Hệ thống thấy độ ẩm đất đã đủ nên **bỏ qua lần tưới này**.
3. **Tình huống 3 (Quan trọng) — AI dự báo CÓ MƯA $\rightarrow$ Hoãn tưới, sau đó đất vẫn khô $\rightarrow$ Tự động tưới bù**:
   - **Giai đoạn A (Hoãn tưới chờ mưa)**: Lau khô đầu dò cảm biến đất (`< 30%`), chọn dự báo thời tiết **`Có mưa`**. Khi đến mốc lịch, mặc dù đất đang khô nhưng vì dự báo sắp mưa nên hệ thống **hoãn/chặn chưa tưới** để chờ mưa tự nhiên.
   - **Giai đoạn B (Hết thời gian chờ nhưng chưa mưa, đất vẫn khô $\rightarrow$ Tưới bù)**: Sau khoảng thời gian chờ (1 phút tiếp theo), trời thực tế không mưa (chuyển trạng thái sang **`Không mưa`**) và cảm biến xác nhận **đất vẫn còn khô (`< 30%`)** $\rightarrow$ Hệ thống **tự động kích hoạt bật bơm tưới bù** cho cây.

### Bước 3: Demo ESP32-CAM Livestream & AI Phân tích Lá / Quả (`/ai`)
- Mở **Livestream trực tiếp từ ESP32-CAM** trên giao diện Web để quan sát cây theo thời gian thực.
- Chụp ảnh (hoặc hệ thống chụp định kỳ) gửi về Backend để AI phân tích:
  - **Model YOLO**: Phát hiện vị trí, đếm tổng số quả trên cây và phân loại quả **`Xanh`** / **`Chín`**.
  - **Model MobileNetV3**: Phân tích lá cây để nhận diện lá khỏe hoặc các bệnh trên lá cà chua.
- Người dùng có thể theo dõi tình trạng cây mọi lúc mọi nơi trên Web Dashboard.
