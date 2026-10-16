# XGBoost — dự báo giáng thủy 60 phút

Pipeline AI thời tiết độc lập trong `ml/weather/`: dữ liệu → đặc trưng nhân quả → chia
tập theo thời gian → XGBoost → calibration → threshold → artifact/inference.
Backend vẫn dùng Mock AI. Không gọi MQTT hoặc điều khiển bơm từ pipeline này.

## Chạy trên Google Colab CPU

Làm theo [hướng dẫn chung](../README.md): upload source ZIP và `open-meteo.csv`
vào `MyDrive/aiot-plant-care/`, mở `notebooks/train_xgboost_rain.ipynb` trên hosted
Colab Python 3.12, chọn CPU runtime. Không train trên Windows; không cần GPU cho XGBoost.

Bootstrap cài `../requirements-colab.lock`, giữ Torch của runtime. Nếu báo module cũ,
restart session rồi chạy lại từ đầu. Mặc định đọc CSV từ Drive; có thể chọn
`DATA_SOURCE="download"` tải Open-Meteo ngay trong Colab. Đặt biến `.env.example`
qua `os.environ` trước cell dữ liệu; file env không tự được nạp.

Chạy audit, split theo thời gian và smoke tối đa 20 rounds. Sau rà soát,
bật `DATA_REVIEWED=True`, `RUN_FULL_TRAINING=True` để train tối đa 500 rounds.
`FINAL_EVALUATION` mặc định tắt; chỉ mở Test sau khi chốt cấu hình bằng Validation.
Artifacts lưu vào Drive `aiot-plant-care/weather-runs/`. XGBoost chưa resume giữa rounds;
runtime ngắt trước khi xuất artifact thì tạo run mới cùng seed.

CLI có thể chạy từ thư mục bài toán trong Colab. `scripts/build_notebook.py` tái tạo notebook,
từ chối ghi đè notebook có cell nếu không có `--force`.

## Dữ liệu và ý nghĩa nhãn

Nguồn: [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api).
CSV đã có: `data/raw/open-meteo.csv`; URL tải gốc trong `ml/README.md` chỉ định
ECMWF IFS, tọa độ yêu cầu 20.96/105.76. Metadata CSV ghi ô lưới
20.984182/105.79108, cao độ 8 m, UTC+7 (`Asia/Bangkok`). Đây là điểm nghiên cứu từ
nguồn dữ liệu, chưa khẳng định là tọa độ cây thực tế. CSV không chứa tên model,
vì vậy provenance không giả nhận biết model ngoài thông tin URL đã ghi.
Đổi UTC khi đọc; giao diện/chu kỳ ngày dùng `Asia/Ho_Chi_Minh`. Gió km/h đổi sang m/s.

**Nhãn v1:** tại mốc đúng giờ `t`, `y=1` khi `precipitation[t+1h] >= 0.1 mm`.
Theo Open-Meteo, `precipitation[t]` là tổng **giờ vừa qua**, gồm mưa, mưa rào và tuyết.
Vì vậy cửa sổ nhãn là `(t,t+60 phút]`; đây là nhãn **giáng thủy đại diện cho mưa**,
không tự đổi tên thành phép đo mưa thuần. Metadata và kết quả có `rain_proxy=true`.
Muốn mục tiêu chỉ mưa lỏng phải lấy biến `rain`, đổi schema/nhãn và huấn luyện lại.
Không chọn ngưỡng nhãn bằng kết quả Test.

Dữ liệu archive là tái dựng lịch sử; giá trị có thể được sửa hoặc tổng hợp sau thời
điểm mà ta giả định dự báo. Chia tập theo timestamp tránh rò rỉ trong pipeline nhưng
**không chứng minh nguồn lịch sử đã sẵn có tại thời điểm vận hành**. Kết quả hồi cứu
không phải chứng nhận chất lượng nowcast live. Cần thu thập snapshot API có thời điểm
nhận thật và nhãn đo mưa tại địa điểm để đánh giá ngoài nguồn trước khi triển khai.
Ghi công Open-Meteo và tuân thủ [điều khoản/giấy phép](https://open-meteo.com/en/terms)
trước khi chia sẻ dữ liệu. Dataset, outputs và UBJ không nằm trong Git.

## Đặc trưng và kiểm tra rò rỉ

- Năm biến tại `t`: nhiệt độ °C, độ ẩm %, áp suất mặt đất hPa, mây %, gió m/s.
- Với từng biến: lag 1h/3h, trung bình trailing 6h (bao gồm t), thay đổi so với 3h trước.
- Chu kỳ giờ/ngày trong năm bằng sin/cos: tổng 29 đặc trưng.
- Không dùng giáng thủy tương lai, cột nhãn hoặc dữ liệu forecast phát hành sau t làm đặc trưng.
  Giáng thủy giờ trước chỉ dùng baseline persistence, không phải feature của model.
- Kiểm tra đơn vị/header, UTC, giới hạn vật lý, timestamp trùng và giờ thiếu.
  Timestamp trùng giống nhau được gộp; trùng khác giá trị bị từ chối.
  Giá trị thiếu/ngoài giới hạn thành NaN; không bfill/interpolate. Reindex đủ lưới giờ
  trước shift/rolling để lag đúng theo thời gian. Loại cửa sổ feature/nhãn không đủ dữ liệu.
  Giờ cuối không có nhãn tương lai bị loại, không gán thành không mưa.
- Chia theo thứ tự: Train 60%, early-stop 10%, calibration 10%, threshold 10%, Test 10%.
  Ba block ở giữa thuộc Validation. Embargo 6h và loại nhãn vượt ranh giới; tỷ lệ thực tế
  giảm nhẹ sau embargo. Không shuffle/stratified random split.

## Training và đánh giá

XGBoost `hist`, depth 4, learning rate 0.05, subsample/colsample 0.9.
Trọng số lớp lấy tỷ lệ âm/dương từ **Train**. Early stopping theo AUCPR ở block
`early_stop`, patience 30, lưu best booster bằng callback `save_best=True`.
Sigmoid trên logit học ở block `calibration`; chọn threshold F1 lớp dương trên lưới
0.05–0.95 ở block `threshold`. Không fit calibrator vào Test.

Metrics: accuracy, precision, recall, F1, macro F1, average precision, PR-AUC tích phân
hình thang, ROC-AUC, Brier và log loss; classification report, confusion matrix,
đường PR, calibration và lịch sử logloss/AUCPR. Average precision và PR-AUC hình thang
được đặt tên riêng vì không hoàn toàn giống nhau. Baseline: luôn khô, prior Train,
persistence giờ trước (báo coverage nếu thiếu giáng thủy giờ trước).
Validation đã dùng để chọn ngưỡng nên có thể lạc quan; Test giữ riêng mới là đánh giá cuối.
Nếu tập chỉ có một lớp, metric xếp hạng không xác định được ghi null; các block học
bắt buộc có hai lớp và sẽ báo lỗi rõ ràng nếu thiếu.

## Artifact và inference

Run gồm `model.ubj`, `calibration.json`, `feature_config.json`, `metadata.json`, checksum,
phiên bản thư viện, nguồn dữ liệu, fingerprint, timestamp từng tập, feature importance,
history/biểu đồ và báo cáo. Dùng UBJ/JSON, không dùng pickle. Chỉ load artifact tin cậy.
Test yêu cầu dữ liệu cùng fingerprint, mặc định khóa và từ chối đánh giá lại run đã có báo cáo.

```python
from weather_training.inference import RainPredictor

predictor = RainPredictor("outputs/<ten-run>")
t = hourly.index[-1]
result = predictor.predict(hourly.loc[:t], as_of=t, source="open_meteo_archive")
```

Ví dụ này là replay lịch sử, không phải dự báo hôm nay. Kết quả chứa version, source,
`predicted_at` UTC, cửa sổ dự báo, xác suất, threshold, `historical_replay`, `rain_proxy`,
`smoke_only` và `production_ready=false`. Thiếu model hoặc lịch sử thì báo lỗi, không giả kết quả.
`as_of` là mốc đúng giờ; cửa sổ dự báo bắt đầu tại mốc đó, không tự trượt 60 phút từ lúc gọi API.

Adapter [OpenWeather Current](https://openweathermap.org/api/current):
`openweather_snapshot(payload, received_at=..., units="metric")` giữ thời điểm đo và nhận.
Phải có `main.grnd_level`; **không thay áp suất mặt đất bằng main.pressure mực biển**.
Không coi `rain.1h` của provider này là biến huấn luyện vì khác contract và có thể thiếu.
`snapshots_to_hourly(..., as_of=t)` chọn snapshot đã nhận trước từng mốc giờ, còn mới
tối đa 15 phút, đủ sáu giờ. Dữ liệu đến muộn không được chèn ngược như thể đã có sẵn.
Nguồn khác/cảm biến thiếu mây-gió không được dùng thay tự động.

`predict(..., source="openweather_current")` mặc định từ chối vì domain shift chưa đánh giá;
`allow_domain_shift=True` chỉ cho thử nghiệm sau chuẩn hóa, vẫn trả production_ready=false.
Mốc live phải nằm trong 15 phút vừa qua, input không chứa tương lai hoặc snapshot cũ.
Chỉ dùng đúng địa điểm của model; chưa hỗ trợ chuyển địa điểm/mô hình đa vùng.
Chưa có collector API, lịch thu thập hoặc adapter backend: bước tích hợp sau phải giữ
worker cùng codebase, lưu snapshot/metadata qua schema Alembic và đánh giá live riêng.

## Kiểm chứng

Chạy pytest/Ruff **trong Colab** theo [hướng dẫn kiểm thử chung](../README.md).

Tests dùng dữ liệu tổng hợp kiểm tra cửa sổ nhãn, tương lai, khoảng trống, timestamp
mâu thuẫn, UTC/đơn vị, embargo, snapshot đến muộn, checksum, xuất/load/inference và khóa Test.
Đánh giá Test trong unit test chỉ dùng dữ liệu tổng hợp, không mở Test của CSV thực tế.

Kiểm chứng lịch sử trước Colab — audit CSV local ngày 14/10/2026: **50.616 giờ**, không thiếu giờ, không timestamp
trùng, không giá trị thiếu hoặc ngoài giới hạn. Sau lookback và nhãn tương lai còn
**50.610 mẫu**. Smoke trên 365 ngày đầu với tối đa 20 rounds đã xuất/load model và
inference thành công trên CPU. Chưa chạy full training, chưa mở Test của dữ liệu thật,
chưa gọi OpenWeather live.
Tải trực tiếp Open-Meteo đã kiểm tra với 7 ngày (168 giờ), đúng đơn vị và không thiếu giờ.
Ở block Validation của smoke, F1 model khoảng 0,703, thấp hơn persistence khoảng 0,801;
đây là smoke ít vòng trên một năm, chưa có cơ sở tuyên bố XGBoost tốt hơn baseline.

Tham khảo: [XGBoost early stopping/save_best](https://xgboost.readthedocs.io/en/stable/python/python_api.html),
[Open-Meteo quy ước hourly](https://open-meteo.com/en/docs/historical-weather-api),
[OpenWeather trường dữ liệu](https://openweathermap.org/api/current).
