# Thời tiết DEMO/LIVE và lịch tưới — 16/10/2026

## Hai chế độ thời tiết

Chọn ở thẻ **Thời tiết quyết định tưới** trong Tổng quan hoặc Phân tích AI.

| Chế độ | Đầu vào | Quyết định tại lịch tưới |
| --- | --- | --- |
| DEMO → Có mưa | Kịch bản chọn trên dashboard | Bỏ qua lần tưới tự động |
| DEMO → Không mưa | Kịch bản chọn trên dashboard | Có thể tưới nếu đất khô và đủ điều kiện an toàn |
| LIVE | Dự báo XGBoost thật từ lịch sử OpenWeather | Có mưa: bỏ qua; ít mưa: kiểm tra đất/an toàn; chưa có dự báo hợp lệ: chờ có giới hạn |

DEMO dùng để trình diễn quy tắc, **không phải kết quả XGBoost**, không tạo xác suất
0%/100% giả, không ghi quan trắc hoặc inference giả. Chạy được khi không có Internet,
không cần lịch sử 6 giờ hoặc weights mưa. PostgreSQL, MQTT và API vẫn cần hoạt động.
Chỉ cho phép DEMO với thiết bị simulation. MobileNetV3/YOLO vẫn chạy model thật.

LIVE chỉ áp dụng cho **nguồn thời tiết**, không bật firmware/bơm vật lý. Thiết bị vẫn
là `sim-esp32-01`. LIVE không tự chuyển sang DEMO khi lỗi hoặc thiếu dữ liệu.

Chọn chế độ/kịch bản được lưu trong bảng `weather_control`; tải lại trang hay restart
vẫn giữ nguyên. Nâng cấp từ phiên bản cũ giữ mặc định LIVE. Đổi chế độ không tự bật
quy tắc tưới, không xóa lịch sử và không dừng lệnh bơm đã được chấp nhận; dùng **Dừng bơm**.

Worker tiếp tục lấy OpenWeather mỗi 60 giây ở cả hai chế độ khi có mạng/cấu hình.
Khi đang LIVE, worker tự xếp một job XGBoost cho mỗi mốc giờ/địa điểm/version nếu
đầu vào hợp lệ, trong 15 phút đầu giờ. Dự báo có hiệu lực tới hết horizon 60 phút.
Không lấy dự báo lịch sử, fixture, Mock, sai tọa độ, sai version hoặc quá hạn để tưới.
Đánh giá sai lệch nguồn OpenWeather so với train Open-Meteo vẫn chưa hoàn tất.

## Thủ công và tự động theo lịch

- **Thủ công:** tắt checkbox tự động rồi dùng **Tưới ngay**/**Dừng bơm**.
  Lệnh thủ công bỏ qua cổng kiểm tra mưa/lịch, vẫn kiểm tra MQTT, thiết bị, độ mới
  cảm biến, hiệu chuẩn đất, trạng thái OFF, lệnh đang chạy, cooldown và duration.
- **Tự động:** bật checkbox **Bật tự động theo lịch, có kiểm tra thời tiết**, chọn:
  - Hằng ngày theo giờ Việt Nam: danh sách `HH:MM`, ví dụ `06:00, 18:00`.
  - Chu kỳ: lặp mỗi 1–1440 phút; lần đầu sau một chu kỳ từ lúc lưu thay đổi.
  Sau đó đặt ngưỡng đất, thời lượng 1–15 giây và thời gian nghỉ 30–3600 giây.
  Nút thủ công vẫn có thể dùng để can thiệp khi tự động đang bật.

Đến lịch: kiểm tra đất khô → thời tiết cho phép → kiểm tra an toàn → tạo command.
AI không gửi MQTT hoặc bật bơm trực tiếp. Một lịch chỉ được xử lý một lần, kể cả khi
restart; lịch đã bỏ lỡ không được tưới bù. Thay đổi cấu hình quy tắc bắt đầu lại lịch
từ lúc lưu, không thực thi lại một giờ đã trôi qua.

Cửa sổ thực thi tối đa 5 phút sau giờ đã đặt, hoặc một chu kỳ nếu chu kỳ ngắn hơn.
Nếu LIVE chưa có dự báo hợp lệ, hệ thống chờ trong cửa sổ này để worker kịp trả
dự báo đầu giờ; hết cửa sổ sẽ bỏ qua. Mưa, đất đủ ẩm, cảm biến cũ, mất MQTT hoặc
cooldown chặn sẽ bỏ qua lần đó và ghi lý do, không liên tục thử bật bơm trong cùng lịch.
Sau khi đổi DEMO từ mưa sang không mưa, chờ **lịch tiếp theo** nếu lần trước đã bị bỏ qua.

Dashboard hiển thị kết quả lần lịch gần nhất. Command tự động lưu `weather_context`
(nguồn, mode, mưa/không mưa, version/job LIVE, mốc lịch) để đối chiếu sau đó.
Việc đánh dấu lịch và việc kiểm tra trước command dùng khóa DB; đổi mode hoặc quy tắc
giữa lúc đánh giá và gửi sẽ được kiểm tra lại.

## Trình diễn nhanh trên trường

1. Chọn thời tiết **DEMO → Có mưa** và kịch bản cảm biến **Đất khô**.
2. Đặt lịch **chu kỳ 1 phút**, ngưỡng **30%**, thời lượng **1 giây**, nghỉ **30 giây**;
   bật tự động và lưu.
3. Đợi tới lịch: dashboard báo có mưa, không tạo lệnh tưới tự động.
4. Chọn **Không mưa**, đợi lịch tiếp theo: nếu thiết bị online/đất còn khô,
   lệnh đi qua Đã gửi → ACK → Hoàn tất. Không cần mạng ngoài để thực hiện kịch bản này.
5. Chọn **LIVE** để xem dự báo thực tế hoặc lý do chưa khả dụng; không dùng DEMO bù
   nếu thiếu dữ liệu. Kết thúc buổi demo bằng tắt tự động và **Dừng bơm**.

Kịch bản cảm biến **Trời mưa** chỉ đổi telemetry mô phỏng, không tự đổi chế độ thời tiết;
dùng hai nút có mưa/không mưa trong thẻ thời tiết để demo cổng kiểm tra tưới.

## API và migration

- `PUT /api/v1/weather/mode`: `{ "mode": "demo" | "live", "demo_rain": true | false }`.
- `GET /api/v1/dashboard`: thêm `weather_control` (mode, source, trạng thái, lý do,
  rain_expected nullable, rain_probability nullable, version/job và horizon).
  `weather_status` vẫn biểu diễn khả năng tạo job LIVE tại thời điểm hiện tại.
- `PUT /api/v1/irrigation/policy`: thêm `schedule_kind` (`daily`/`interval`),
  `daily_times` (1–24 giờ HH:MM), `interval_minutes` (1–1440).
- `POST /api/v1/ai/weather`: chỉ tạo/lấy job LIVE, từ chối khi đang chọn DEMO.
- Alembic `d82003` thêm `weather_control`, các trường lịch/last status vào policy
  và `weather_context` vào command. Không sửa hoặc xóa quan trắc cũ.

Thay đổi chính: `app/modules/weather_control.py`, `schedule.py`, `control.py`,
API/schema/ORM/migration, worker; frontend `weather-panel.tsx`, form lịch,
type và dashboard. Không thêm dependency. Chạy/dừng bằng script demo như trước.

## Kiểm chứng 16/10/2026

- `uv sync --locked`, Ruff: đạt. `RUN_E2E=1 uv run pytest -q`: 32 test đạt,
  gồm lượt lịch DEMO có mưa bị chặn, lượt không mưa được tưới trên simulator,
  thủ công bỏ qua mưa nhưng giữ safety, kiểm tra lịch lặp và dự báo LIVE quá hạn.
- `npm ci`, `npm run lint`, `npm run build`: đạt; `npm run test:e2e`: 1 test đạt,
  kiểm tra đổi mode, giữ lựa chọn sau reload, form lịch, upload AI và giao diện mobile.
  Hai bộ E2E chạy tuần tự, cùng simulator.
- Alembic ở `d82003`, `alembic check` không phát hiện thiếu migration.
  Compose config hợp lệ; PostgreSQL, MinIO, MQTT healthy, MinIO init thoát mã 0.
- Chưa kiểm chứng phần cứng/bơm thật hoặc độ chính xác XGBoost với thời tiết địa phương.
