# Fruit Health — phát hiện quả và phân loại độ chín

Pipeline độc lập dùng **YOLO11n pretrained COCO**, fine-tune hai lớp `0=ripe`,
`1=unripe` bằng Ultralytics/PyTorch. Đếm số quả trong từng ảnh; không tracking qua
thời gian. Tên thư mục giữ theo dự án; **chưa nhận diện bệnh quả**.

Mở [notebook train_yolo_fruit.ipynb](notebooks/train_yolo_fruit.ipynb), chạy các cell
từ trên xuống bằng kernel đã chọn. Notebook có nội dung thực thi, không phải khung trống.
Pipeline ở `fruit_training/`; notebook chỉ cấu hình, chạy thí nghiệm và hiển thị.

## Dataset và audit thực tế

Nguồn: [Tomato Ripeness, Roboflow v4](https://universe.roboflow.com/uni-zkvyc/tomato-ripeness-um0yl/dataset/4),
workspace `uni-zkvyc`, project `tomato-ripeness-um0yl`, **CC BY 4.0**.
Khi chia sẻ dữ liệu/phái sinh, giữ ghi công và thông tin nguồn/giấy phép.
Raw ở `data/raw/tomato-ripeness-v4/`, cấu trúc `train|valid|test/images` và `labels`.
Dataset đã auto-orient và stretch 640×640 từ nguồn; không có augmentation trong export.

Đã kiểm tra 8.409 ảnh/nhãn trên máy cá nhân. **17.875 annotation là polygon**,
được chuyển thành bbox bao ngoài chuẩn YOLO detection trong thư mục prepared riêng.
Không sửa ảnh hay nhãn raw. Bbox bao ngoài có thể chứa nền và chồng lấn nhiều hơn polygon.

| Tập | Raw | Sau chính sách lọc mặc định | Box ripe | Box unripe |
| --- | ---: | ---: | ---: | ---: |
| Train | 5.466 | 1.414 | 2.045 | 1.105 |
| Validation | 1.682 | 860 | 1.078 | 818 |
| Test | 1.261 | 1.254 | 1.461 | 1.424 |

Audit không phát hiện ảnh hỏng/nhãn sai hình học, nhưng cách ly **50 ảnh trùng pixel
có nhãn mâu thuẫn** và loại **4.831 ảnh ở phía split có ưu tiên thấp hơn**.
Các con số trên phụ thuộc chính sách grouping hiện tại, chưa phải chứng nhận dataset sạch.

Chính sách chống rò rỉ:

- Nhóm theo SHA256 file, RGB pixel, tên gốc trước hậu tố `.rf.<hash>`, pHash 64 bit
  với khoảng cách Hamming ≤ 4; có thể bổ sung CSV `path,group_id` cho cảnh/cây/video.
- Giữ split gốc, ưu tiên Test > Validation > Train cho nhóm xuất hiện ở nhiều tập;
  loại bản thuộc tập ưu tiên thấp hơn. Không chuyển ảnh Test sang Train.
- Loại ảnh trùng pixel trong split; cách ly mọi bản có nhãn xung đột.
- Manifest lưu hash nguồn, box chuyển đổi và group; kiểm tra lại trước train/inference evaluation.

**Cần rà soát trước full training:** có 1.458 nhóm giao nhiều split, 1.018 cặp pHash
gần nhau và nhóm lớn nhất có 570 ảnh. Tên file chung như số thứ tự có thể gom nhầm;
quan hệ gần trùng có tính bắc cầu cũng làm nhóm lớn. pHash có thể bỏ sót góc nhìn/crop
cùng cây. Xem `audit.json` phần `groups`, `near_pairs`, `excluded`, kiểm tra ảnh và
metadata nguồn, rồi cố định chính sách trước thí nghiệm. CSV chỉ bổ sung grouping,
không tự tách nhóm heuristic; nếu có bằng chứng gom nhầm cần sửa rule, audit lại và
tạo run mới. Không điều chỉnh rule theo kết quả Test.

Không có ảnh nền âm tính sau audit. Cần bổ sung và đánh giá ảnh không có cà chua,
ánh sáng thực tế, che khuất và camera ESP32-CAM. Lệch lớp ở mức vừa phải; hiện không
oversample để tránh lặp cảnh. Theo dõi AP/recall từng lớp trên Validation trước khi đổi sampling.

## Huấn luyện trên Google Colab GPU

Làm theo [hướng dẫn chung](../README.md): upload source ZIP và `tomato-ripeness-v4.zip`
lên Drive, mở `notebooks/train_yolo_fruit.ipynb` với hosted Colab Python 3.12 + GPU.
Mặc định giải nén dataset vào `/content/aiot-data/tomato-ripeness-v4`, chạy audit,
smoke 1 epoch (16 Train + 16 Validation, 320×320, batch 4).
Tùy chọn tải Roboflow dùng Colab Secrets `ROBOFLOW_API_KEY`.

Sau khi rà soát dữ liệu, bật `DATA_REVIEWED=True`, `RUN_FULL=True`:
tối đa 80 epoch, 640×640, batch 16, workers 2, CUDA/AMP, seed 42.
Giảm batch nếu OOM trong run mới. Chỉ bật `RUN_FINAL_TEST` sau khi chốt Validation.
Checkpoint/báo cáo lưu tại Drive `aiot-plant-care/fruit-runs/`.

Resume: chạy lại bootstrap/dataset/audit, gán `FULL_RUN` về run Drive cũ và `RESUME=True`.
Prepared-full giữ đường dẫn cố định `/content/aiot-data/fruit-prepared-full/data.yaml`.
Pipeline kiểm tra fingerprint, cấu hình; không resume run đã hoàn tất/strip optimizer.
Drive có thể chưa đồng bộ lần ghi cuối khi runtime ngắt đột ngột.

Dependency bổ sung dùng `../requirements-colab.lock`, giữ Torch/CUDA runtime.
Biến tùy chọn ở `.env.example`, đặt qua `os.environ`; không tự nạp file env.
CLI vẫn có thể chạy từ thư mục bài toán **trong Colab**, nhưng notebook là luồng chính.

## Training, metrics và artifact

Fine-tune toàn bộ YOLO11n, AdamW, LR 0,001, seed/deterministic, AMP khi CUDA khả dụng.
Early stopping 15 epoch; Ultralytics 8.3.203 chọn best bằng **Validation mAP50–95**.
Augmentation chỉ Train: flip ngang, dịch/chỉnh kích thước nhẹ, mosaic 0,25;
hue=0, saturation/value nhẹ để hạn chế làm đổi nhãn độ chín. Không augment Val/Test.
Deterministic không đảm bảo giống từng bit giữa hardware/runtime khác nhau.

- `yolo/results.csv`, `results.png`: box/cls/DFL loss Train/Val, precision/recall/mAP.
- `evaluation/val|test/`: confusion matrix, đường PR/F1 và ảnh ví dụ.
- `val_metrics.json`, `test_metrics.json`: mAP50, mAP50–95, P/R từng lớp và MAE/RMSE/bias đếm.
- P/R Ultralytics là thống kê tại điểm F1 tốt nhất trên tập đang đánh giá;
  không phải P/R ở ngưỡng đếm 0,25, không dùng điểm đó trên Test để chọn ngưỡng.
- Đếm dùng cố định confidence 0,25, NMS IoU 0,7, max_det 300 từ artifact.
  Chạm max_det được báo `count_may_be_truncated`; chưa tracking danh tính quả.

Bundle cho inference: `best.pt`, `class_mapping.json`, `preprocessing.json`, `metadata.json`.
Metadata chứa version, UTC, nhãn, nguồn dữ liệu, fingerprint, checksum, cấu hình và giới hạn.
`dataset_manifest.json`, `run_config.json` lưu khả năng truy vết. Không tự tạo dự đoán khi thiếu model.

```python
from fruit_training.inference import FruitPredictor

predictor = FruitPredictor("outputs/<completed-run>")  # chỉ load artifact tin cậy
result = predictor.predict("tomato.jpg", source="esp32_cam", annotated_path="outputs/prediction.jpg")
print(result["counts_by_class"], result["detections"])
```

Output có bbox xyxy pixel và chuẩn hóa, confidence từng detection, version, nguồn và UTC.
Preprocessing inference: EXIF orientation, RGB, letterbox, scale 1/255. Raw train vốn đã
stretch 640×640 nên khác ảnh camera theo tỷ lệ khác; cần đo ảnh hưởng trước triển khai.
Backend sau này có thể load predictor một lần trong worker riêng; hiện **chưa thay Mock AI**,
không sửa API/DB, không tự chạy inference nặng trong request, không điều khiển bơm.

## Kiểm chứng và giới hạn hiện tại

Kiểm chứng lịch sử trước Colab: audit dataset thực, Ruff (pass), 12 unit test (pass) và smoke 1 epoch trên CPU
laptop, 16+16 ảnh. Notebook có 18 cell, đã kiểm tra schema/cú pháp và chạy hai cell
khởi tạo trên kernel local của phiên bản notebook cũ.
Smoke kiểm tra train → best weights → evaluate → load artifact → inference.
Smoke Validation mAP50 ≈ 0,215; mAP50–95 ≈ 0,117; **không dùng các số này làm chất lượng model**.
Ở ngưỡng 0,25 smoke có thể chưa phát hiện quả nào. Chưa full training, chưa chạy Test,
chưa đánh giá camera thật.

`scripts/build_notebook.py` tái tạo notebook có nội dung nếu cần. Chỉ chạy khi muốn khôi phục
bản chuẩn; cần `--force` để ghi đè notebook có cell, nên lưu riêng thay đổi thí nghiệm trước.
`.gitignore` bỏ qua data/checkpoints/outputs/dist/.venv/runtime. Không commit dataset hoặc weights.
Ultralytics dùng giấy phép AGPL-3.0/Enterprise; tham khảo [nguồn chính thức](https://docs.ultralytics.com/).
Mô hình: [YOLO11](https://docs.ultralytics.com/models/yolo11/),
[train](https://docs.ultralytics.com/modes/train/), [validation](https://docs.ultralytics.com/modes/val/).
