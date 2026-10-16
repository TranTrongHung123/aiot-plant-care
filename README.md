# AIoT Plant Care

**Hệ thống chăm sóc cây cà chua thông minh ứng dụng IoT và AI**

## 1. Giới thiệu

AIoT Plant Care là dự án xây dựng hệ thống giám sát và chăm sóc **cây cà chua** thông minh, kết hợp Internet of Things, Machine Learning và Computer Vision.

Hệ thống sử dụng các cảm biến để theo dõi điều kiện môi trường, tự động điều khiển tưới nước và ứng dụng AI để dự báo khả năng mưa, nhận diện bệnh trên lá, phát hiện và theo dõi quả cà chua.

Thông qua Web Dashboard, người dùng có thể theo dõi dữ liệu theo thời gian thực, điều khiển thiết bị, quản lý hoạt động tưới và xem kết quả phân tích AI.

Dự án hướng đến việc sử dụng nước hợp lý, hỗ trợ chăm sóc cây và phát hiện sớm những dấu hiệu bất thường trong quá trình sinh trưởng.

## 2. Chức năng chính

- **Giám sát môi trường:** Thu thập nhiệt độ, độ ẩm không khí, độ ẩm đất và áp suất khí quyển từ các cảm biến.
- **Tưới nước thông minh:** Tự động bật/tắt máy bơm dựa trên độ ẩm đất, điều kiện thời tiết và các quy tắc tưới; hỗ trợ điều khiển thủ công qua website.
- **AI dự báo mưa:** Sử dụng XGBoost để dự đoán khả năng mưa trong **60 phút tiếp theo**, hỗ trợ quyết định tưới phù hợp.
- **AI nhận diện bệnh trên lá cà chua:** Sử dụng MobileNetV3 để phân loại tình trạng lá khỏe và một số bệnh trên lá từ hình ảnh.
- **AI theo dõi quả cà chua:** Sử dụng YOLO để phát hiện vị trí, đếm số lượng quả và phân loại mức độ chín (ví dụ: xanh/chín) trong ảnh.
- **AI phân tích bất thường trên quả (mở rộng):** Nghiên cứu mô hình phân loại tình trạng sức khỏe và một số dấu hiệu bệnh hoặc hư hỏng trên ảnh từng quả cà chua.
- **Web Dashboard:** Hiển thị dữ liệu thời gian thực, biểu đồ môi trường, trạng thái thiết bị, lịch sử tưới, hình ảnh cây và kết quả AI.
- **Lưu trữ và thống kê:** Lưu lịch sử cảm biến, hoạt động tưới, hình ảnh lá/quả và kết quả phân tích để theo dõi quá trình chăm sóc cây.
- **Mô phỏng IoT:** Giả lập cảm biến, camera và máy bơm để phát triển, kiểm thử phần mềm trước khi kết nối phần cứng thực tế.

## 3. Công nghệ sử dụng

### Hardware & IoT

- **ESP32-WROOM-32:** Thu thập dữ liệu cảm biến, điều khiển máy bơm và giao tiếp với hệ thống.
- **ESP32-CAM OV2640:** Chụp ảnh lá và quả cà chua, gửi về Backend để xử lý AI.
- **DHT22:** Đo nhiệt độ và độ ẩm không khí.
- **BMP180:** Đo áp suất khí quyển.
- **Soil Moisture Sensor:** Đo độ ẩm đất.
- **Relay 12V & DC Water Pump:** Bật/tắt máy bơm phục vụ tưới nước.
- **OLED I2C:** Hiển thị dữ liệu và trạng thái hệ thống tại thiết bị.
- **MQTT, HTTP/HTTPS:** Truyền dữ liệu, lệnh điều khiển và hình ảnh.

### Software

| Thành phần              | Công nghệ                          |
| ----------------------- | ---------------------------------- |
| Frontend                | Next.js, TypeScript, Tailwind CSS  |
| Backend                 | Python, FastAPI                    |
| Database                | PostgreSQL                         |
| Object Storage          | MinIO                              |
| ORM & Migration         | SQLAlchemy, Alembic                |
| Real-time Communication | WebSocket                          |
| MQTT Broker             | HiveMQ Cloud                       |
| Firmware                | C++, Arduino Framework, PlatformIO |
| Deployment              | Docker Compose                     |

### AI & Machine Learning

| Bài toán                                    | Phương pháp                                  | Công nghệ                         |
| ------------------------------------------- | -------------------------------------------- | --------------------------------- |
| Dự báo khả năng mưa trong 60 phút           | Binary Classification                        | XGBoost, Scikit-learn             |
| Nhận diện bệnh trên lá cà chua              | Multi-class Image Classification             | PyTorch, MobileNetV3              |
| Phát hiện, đếm và phân loại độ chín của quả | Object Detection                             | YOLO                              |
| Nhận diện bất thường trên quả (mở rộng)     | Image Classification                         | PyTorch, MobileNetV3              |
| Hỗ trợ quyết định tưới                      | Rule-based Decision System kết hợp dự báo AI | Python, FastAPI                   |
| Xử lý hình ảnh                              | Image Processing                             | OpenCV, Pillow                    |
| Dữ liệu thời tiết lịch sử                   | Weather Data API                             | Open-Meteo Historical Weather API |
| Dữ liệu thời tiết trực tuyến                | Weather API                                  | OpenWeatherMap API                |

## 4. Kiến trúc tổng quan

Hệ thống gồm các thành phần chính:

- **IoT Devices:** ESP32 thu thập dữ liệu môi trường và điều khiển tưới; ESP32-CAM chụp ảnh lá và quả.
- **Backend:** FastAPI tiếp nhận dữ liệu, xử lý nghiệp vụ, giao tiếp MQTT, cung cấp REST API và kết nối các mô hình AI.
- **AI Models:** XGBoost dự báo mưa; MobileNetV3 phân loại bệnh lá; YOLO phát hiện và phân loại độ chín của quả. Nhận diện bất thường trên quả là hướng mở rộng.
- **Data Storage:** PostgreSQL lưu dữ liệu có cấu trúc; MinIO lưu hình ảnh và tệp liên quan.
- **Frontend:** Next.js cung cấp giao diện giám sát, quản lý và điều khiển.
- **IoT Simulators:** Mô phỏng thiết bị để phát triển và kiểm thử khi chưa kết nối phần cứng thật.

Luồng dữ liệu chính:

1. ESP32 gửi dữ liệu cảm biến và nhận lệnh điều khiển qua **MQTT**.
2. ESP32-CAM chụp ảnh, gửi về **FastAPI qua HTTP/HTTPS** để phân tích trên Backend; không chạy mô hình Deep Learning trực tiếp trên camera.
3. Backend xử lý nghiệp vụ, gọi mô hình AI, lưu kết quả vào **PostgreSQL/MinIO** và cập nhật **Web Dashboard qua WebSocket**.
4. Hệ thống tưới sử dụng quy tắc an toàn, dữ liệu độ ẩm đất và thông tin dự báo mưa để quyết định bật/tắt bơm.

## 5. Cấu trúc dự án

```text
aiot-plant-care/
├── backend/              # FastAPI Backend
├── frontend/             # Next.js Frontend
├── firmware/
│   ├── esp32-sensors/    # ESP32 cảm biến và điều khiển tưới
│   └── esp32-cam/        # ESP32-CAM chụp ảnh
├── ml/
│   ├── weather/          # AI dự báo mưa
│   ├── leaf-disease/     # AI phân loại bệnh trên lá
│   └── fruit-health/     # AI phát hiện và phân tích quả
├── simulators/
│   ├── esp32/            # Mô phỏng cảm biến và máy bơm
│   └── camera/           # Mô phỏng camera
├── docs/                 # Tài liệu dự án
├── compose.yaml          # Docker Compose
└── README.md
```

## 6. Tài liệu

- [System Architecture](docs/architecture.md)
- [Hardware Components](docs/hardware-components.md)
- [Quy định chung cho agent](AGENTS.md)
- [Khởi tạo và vận hành trên Windows](docs/development.md)
- [Huấn luyện AI trên Google Colab](ml/README.md)
- [Hợp đồng dữ liệu dự kiến](docs/contracts.md)
- [Thời tiết DEMO/LIVE và lịch tưới lặp lại](docs/weather-modes.md)

## 7. Trạng thái khởi tạo

Đã triển khai **backend/frontend End-to-End với AI đã huấn luyện**: telemetry MQTT, dashboard
WebSocket, tưới mô phỏng thủ công/tự động có ACK, upload ảnh MinIO và worker xử lý
ba bài toán AI thật (CPU). Dữ liệu và lịch sử lưu thật bằng PostgreSQL/Alembic.

Xem [hướng dẫn demo](docs/demo.md). Chạy từ thư mục gốc:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-demo.ps1
```

Mở `http://localhost:3000`, dùng `APP_TOKEN` trong `.env`. Thiết bị vẫn là simulator.
MobileNetV3/YOLO dùng artifact trong `model-artifacts/`; XGBoost cần cấu hình
OpenWeather và đủ 6 mốc giờ quan trắc. Xem [tích hợp AI](docs/ai-integration.md).
