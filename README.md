# AIoT Plant Care

**Hệ thống chăm sóc cây trồng thông minh ứng dụng IoT và AI**

## 1. Giới thiệu

AIoT Plant Care là dự án xây dựng hệ thống giám sát và chăm sóc cây trồng thông minh, kết hợp Internet of Things (IoT), Machine Learning và Computer Vision.

Hệ thống thu thập dữ liệu môi trường thông qua các cảm biến, tự động điều khiển tưới cây, dự báo khả năng mưa và phân tích hình ảnh để phát hiện dấu hiệu bất thường trên cây trồng.

## 2. Chức năng chính

- **Giám sát môi trường:** Thu thập nhiệt độ, độ ẩm không khí, độ ẩm đất và áp suất khí quyển.
- **Tưới cây thông minh:** Tự động bật/tắt máy bơm dựa trên dữ liệu cảm biến và điều khiển thủ công qua website.
- **AI dự báo mưa:** Sử dụng XGBoost để dự đoán khả năng mưa, hỗ trợ tối ưu lịch tưới.
- **AI nhận diện sức khỏe cây trồng:** Sử dụng ESP32-CAM và Deep Learning để nhận diện một số bệnh trên lá và dấu hiệu bất thường.
- **Web Dashboard:** Giám sát dữ liệu thời gian thực, quản lý thiết bị, lịch tưới, hình ảnh và kết quả AI.
- **Lưu trữ dữ liệu:** Lưu lịch sử cảm biến, hoạt động tưới và hình ảnh cây trồng.

## 3. Công nghệ sử dụng

### Hardware & IoT

- ESP32-WROOM-32
- ESP32-CAM OV2640
- Cảm biến DHT22, BMP180, độ ẩm đất
- Module Relay, máy bơm DC
- Màn hình OLED I2C
- Giao thức MQTT, HTTP/HTTPS

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

| Bài toán              | Công nghệ             |
| --------------------- | --------------------- |
| Dự báo khả năng mưa   | XGBoost, Scikit-learn |
| Nhận diện bệnh lá cây | PyTorch, MobileNetV3  |
| Xử lý hình ảnh        | OpenCV, Pillow        |
| Dữ liệu thời tiết     | OpenWeatherMap API    |
