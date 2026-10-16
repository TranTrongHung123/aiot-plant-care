# MobileNetV3-Large — bệnh/trạng thái lá cà chua

Pipeline PyTorch độc lập; notebook điều phối thí nghiệm trên **Google Colab GPU**.
Backend hiện vẫn dùng Mock AI. Chưa có model đã huấn luyện đầy đủ hoặc đánh giá
ESP32-CAM; không suy diễn kết quả kiểm thử thành độ chính xác mô hình.

## Chạy trên Google Colab

Làm theo [hướng dẫn chung](../README.md): đóng source ZIP, upload lên Drive,
mở `notebooks/train_mobilenetv3.ipynb` với hosted Colab Python 3.12 + GPU.
Không chọn `.venv`/kernel Windows. Dependency chung ở `../requirements-colab.lock`;
Torch/CUDA do runtime quản lý. Nếu bootstrap yêu cầu restart session, restart rồi chạy lại.

Mặc định lấy `MyDrive/aiot-plant-care/plantvillage.zip`, giải nén vào `/content/aiot-data/plantvillage`.
Tùy chọn `DATA_SOURCE="kaggle"` tải trực tiếp bằng KaggleHub; ghim version sau tải.
Checkpoint và báo cáo nằm trên Drive `aiot-plant-care/leaf-runs/`.

Audit → chia tập → smoke 1 epoch (8 ảnh/lớp/tập Train và Validation) → rà soát
→ bật `DATA_REVIEWED=True`, `RUN_FULL_TRAINING=True`. Test chỉ mở khi chốt Validation.
Mặc định batch 32, crop 224, AMP, workers 2; giảm batch khi OOM và tạo run mới.
Chạy lại bootstrap/dataset rồi dùng cell resume với manifest và cấu hình của run cũ.

Biến `COLAB_DRIVE_ROOT`, `LEAF_OUTPUT_ROOT` mô tả trong [../.env.example](../.env.example),
đặt qua `os.environ` trước cell tương ứng, không tự nạp file env.
Script `scripts/build_notebook.py` dùng để tái tạo notebook; yêu cầu `--force` khi ghi đè.

## Dataset, nhãn và rò rỉ

Nguồn: [Kaggle PlantVillage Tomato](https://www.kaggle.com/datasets/charuchaudhry/plantvillage-tomato-leaf-dataset).
Trang Kaggle không trả nội dung metadata giấy phép trong phiên kiểm tra tự động;
cần đọc mục License tại trang nguồn trước khi tái phân phối. Không đóng gói dataset.

Bản local đã kiểm kê có 14.529 ảnh (số file, chưa trừ ảnh lỗi/trùng):

| Thư mục lớp | Ảnh |
| --- | ---: |
| Tomato___Bacterial_spot | 1702 |
| Tomato___Early_blight | 800 |
| Tomato___Late_blight | 1527 |
| Tomato___Leaf_Mold | 761 |
| Tomato___Septoria_leaf_spot | 1417 |
| Tomato___Spider_mites Two-spotted_spider_mite | 1341 |
| Tomato___Target_Spot | 1123 |
| Tomato___Tomato_Yellow_Leaf_Curl_Virus | 4286 |
| Tomato___Tomato_mosaic_virus | 299 |
| Tomato___healthy | 1273 |

- Thứ tự class index là thứ tự tên thư mục được sort; lưu `class_mapping.json`.
- Decode toàn bộ ảnh; lỗi bị loại và ghi tên file/lý do, không xóa ảnh gốc.
- SHA256 bytes và pixel RGB sau EXIF phát hiện ảnh trùng; giữ một ảnh trùng pixel/lớp.
- pHash 64 bit, Hamming <=4, connected components gom gần trùng **trước chia tập**,
  kể cả cặp khác nhãn. Nhóm có nhãn mâu thuẫn bị cách ly để rà soát.
- Có thể cung cấp CSV `path,leaf_id` (path tương đối dataset root, dấu `/`) để gom ảnh
  cùng lá theo metadata đã xác minh. Không coi UUID/tên ảnh là bằng chứng ID chiếc lá.
- Chia 70/15/15 theo **số nhóm của từng lớp**, seed 42; tỷ lệ số ảnh có thể khác vì nhóm
  không cùng kích thước. Ghi manifest cố định, fingerprint và kiểm tra không giao nhóm/hash/leaf_id.
- pHash có false positive và bỏ sót ảnh khác góc, crop hoặc ánh sáng. Không có ID chiếc lá
  thì chưa thể chứng minh split độc lập theo lá; cần rà soát contact sheet/cặp ảnh và bổ sung CSV.
  Dataset có train/val sẵn sẽ được kiểm tra chung và chia lại để ngăn trùng xuyên thư mục.
- Chỉ Train dùng augmentation; Validation/Test/inference dùng RGB, EXIF transpose,
  resize cạnh ngắn 232, center crop 224, tensor /255, ImageNet mean/std.
- Nếu max/min số ảnh Train >=2, weighted cross entropy với trọng số nghịch tần suất Train.
  Không kết hợp thêm oversampling; loss Validation/Test dùng cross entropy thường.

## Huấn luyện, checkpoint và inference

Mặc định 2 epoch train classifier (features và BatchNorm backbone đóng băng), rồi
fine-tune toàn bộ mạng tới tối đa 20 epoch. AdamW, learning rate 1e-3 rồi 1e-4,
batch 32, workers 2, AMP float16 + GradScaler khi CUDA, patience 5. OOM: giảm batch trước
thí nghiệm đầy đủ, tạo run mới và chạy lại smoke. Không tự động giảm batch giữa run.

`best.pt` lưu state_dict của best Validation Macro F1; `last.pt` lưu model, optimizer,
GradScaler, epoch, best score, history và cấu hình. Resume ở ranh giới epoch, seed và
DataLoader seed tính lại theo epoch. Epoch đang dở sẽ chạy lại. Dùng cùng run directory,
config và manifest với `resume=True`; notebook có cell khôi phục từ Drive. Không resume
bằng `best.pt`. File checkpoint ghi tạm rồi replace; kiểm tra đã ghi xong trước khi ngắt runtime; Drive có thể chưa đồng bộ khi mất kết nối.

Artifact gồm weights, class mapping, preprocessing, metadata UTC, checksum, manifest,
environment, history, biểu đồ, metrics/report/confusion matrix, dự đoán từng ảnh.
Chỉ nạp checkpoint do mình tạo/tin cậy; dùng `weights_only=True`.

```python
from leaf_training.inference import Predictor
predictor = Predictor("/path/to/run", device="cpu")
result = predictor.predict("/path/to/new-leaf.jpg", source="manual")
```

Khi tích hợp FastAPI sau này: load một lần trong AI worker cùng codebase, gọi `predict`
với ảnh lấy từ object private, lưu label/confidence/model_version/source/predicted_at.
Không huấn luyện trong request. Softmax là độ tin cậy chưa calibration; mô hình luôn
chọn trong 10 lớp, chưa có reject/OOD. Cần bộ ảnh ESP32-CAM giữ riêng để đánh giá
domain shift trước khi dùng kết quả ngoài thí nghiệm PlantVillage.

## Kiểm chứng

Theo [kiểm thử chung trên Colab](../README.md), chạy pytest/Ruff trong runtime đã bootstrap.
Tests dùng dữ liệu tổng hợp để kiểm tra artifact, resume và rò rỉ; chưa đánh giá chất lượng model.

Các kết quả dưới đây là lịch sử kiểm chứng local trước khi chuyển Colab, không chứng minh
GPU/Drive của Colab đã được chạy. Cần chạy smoke trong notebook ở phiên Colab mới.

Kiểm tra local ngày 13/10/2026: 14.529 ảnh decode được, không có ảnh lỗi;
9 ảnh trùng pixel bị loại và 10 ảnh thuộc nhóm gần trùng mâu thuẫn nhãn bị cách ly
(cần rà soát nhãn, chưa khẳng định nhãn nào đúng). Phát hiện 45 nhóm, 46 cặp pHash.
Còn 14.510 ảnh: Train 10.157 / Validation 2.180 / Test 2.173.
Báo cáo đầy đủ ở `outputs/local-audit/` (ngoài Git).
Smoke local ImageNet 1 epoch, 80 Train + 80 Validation đã chạy thành công, lưu/load
và inference hoạt động. Đây là kết quả kiểm tra CPU trước khi chuyển môi trường CUDA local. Chưa huấn luyện đầy đủ hoặc mở Test.

Tham khảo API: [KaggleHub](https://github.com/Kaggle/kagglehub),
[torchvision MobileNetV3-Large](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.mobilenet_v3_large.html),
[PyTorch AMP](https://docs.pytorch.org/docs/stable/amp.html),
[tái lập](https://docs.pytorch.org/docs/stable/notes/randomness.html).
