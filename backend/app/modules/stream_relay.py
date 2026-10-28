"""Module Relay và Suy luận AI Đa Luồng cho Video Stream ESP32-CAM.

Kiến trúc:
1. Ingestion Thread: Đọc khung hình MJPEG từ ESP32-CAM với Zero-Lag Frame Drop (~20 FPS).
   Không giải mã/nén lại OpenCV trong luồng chính để đạt hiệu năng tối đa (Zero-Recompression).
2. AI Inference Thread: Suy luận không đồng bộ (3-5 FPS) với YOLOv8 và MobileNetV3.
3. WebSocket Broadcast: Truyền tải trực tiếp Binary JPEG frames và JSON metadata (tọa độ BBox + lá)
   tới Web Dashboard để vẽ bằng GPU HTML5 Canvas 60 FPS.
4. HTTP Fallback Generator: Vẫn duy trì tương thích ngược cho endpoint HTTP Multipart (/stream/raw, /stream/ai).
5. Snapshot Extractor: Trích xuất trực tiếp khung hình mới nhất từ stream cho tác vụ AI định kỳ 30 phút.
6. Smart Idle: Tự động dừng luồng khi không có client xem sau 10s để tiết kiệm CPU và làm mát camera.
"""

from __future__ import annotations

import logging
import threading
import time
import unicodedata
import urllib.request
from typing import Generator

import cv2
import numpy as np

from app.modules.trained_ai import predict

log = logging.getLogger(__name__)

DEFAULT_CAM_URL = "http://10.213.128.82:8081/stream"


def _to_ascii(text: str) -> str:
    """Chuyển chuỗi tiếng Việt có dấu sang ASCII không dấu cho cv2.putText (tránh lỗi '???')."""
    text = text.replace("Đ", "D").replace("đ", "d")
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


class StreamRelay:
    def __init__(self, cam_url: str = DEFAULT_CAM_URL) -> None:
        self.cam_url = cam_url
        self._lock = threading.Lock()
        self._running = False
        self._viewer_count = 0
        self._last_viewer_time = 0.0

        self._latest_raw_jpeg: bytes | None = None
        self._latest_ai_jpeg: bytes | None = None
        self._latest_frame_time = 0.0
        self._frame_seq = 0

        self._active_detections: list[dict] = []
        self._active_leaf_info: dict | None = None
        self._latest_ai_time = 0.0

        self._ingestion_thread: threading.Thread | None = None
        self._ai_thread: threading.Thread | None = None

    def set_cam_url(self, url: str) -> None:
        if not url:
            return
        clean_url = url.strip()
        if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            clean_url = f"http://{clean_url}"
        if not clean_url.endswith("/stream"):
            clean_url = clean_url.rstrip("/") + ":8081/stream" if ":8081" not in clean_url else clean_url.rstrip("/") + "/stream"
        with self._lock:
            if self.cam_url != clean_url:
                log.info("Cập nhật URL ESP32-CAM Stream: %s -> %s", self.cam_url, clean_url)
                self.cam_url = clean_url

    def _create_status_frame(self, message: str) -> bytes:
        """Tạo một khung hình JPEG thông báo trạng thái bằng OpenCV."""
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        img[:] = (24, 24, 27)  # Slate-900 background

        # Header bar
        cv2.rectangle(img, (0, 0), (640, 50), (15, 23, 42), -1)
        cv2.putText(
            img,
            "AIoT Plant Care - Stream Relay",
            (20, 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (248, 250, 252),
            2,
            cv2.LINE_AA,
        )

        # Message
        cv2.putText(
            img,
            message,
            (40, 240),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (148, 163, 184),
            2,
            cv2.LINE_AA,
        )
        subtext = f"Nguon: {self.cam_url}"
        cv2.putText(
            img,
            subtext,
            (40, 280),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (100, 116, 139),
            1,
            cv2.LINE_AA,
        )

        _, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return buf.tobytes()

    def add_viewer(self) -> None:
        """Đăng ký thêm 1 client xem (HTTP hoặc WebSocket) và kích hoạt luồng camera."""
        self._ensure_started()

    def remove_viewer(self) -> None:
        """Ghi nhận 1 client ngắt kết nối."""
        self._remove_viewer()

    def _ensure_started(self) -> None:
        """Khởi động ingestion & AI worker threads nếu đang tắt."""
        with self._lock:
            self._viewer_count += 1
            self._last_viewer_time = time.time()
            if self._running:
                return
            self._running = True
            log.info("Khởi động StreamRelay tới: %s (viewers: %d)", self.cam_url, self._viewer_count)

            self._ingestion_thread = threading.Thread(
                target=self._ingestion_loop, name="StreamIngestion", daemon=True
            )
            self._ai_thread = threading.Thread(
                target=self._ai_loop, name="StreamAIInference", daemon=True
            )
            self._ingestion_thread.start()
            self._ai_thread.start()

    def _remove_viewer(self) -> None:
        with self._lock:
            self._viewer_count = max(0, self._viewer_count - 1)
            self._last_viewer_time = time.time()
            log.debug("Viewer disconnected. Con lai: %d viewers", self._viewer_count)

    def _ingestion_loop(self) -> None:
        """Luồng 1: Thu nhận các khung hình MJPEG từ ESP32-CAM với Zero-Lag Frame Drop."""
        placeholder = self._create_status_frame("Dang ket noi toi ESP32-CAM...")
        with self._lock:
            self._latest_raw_jpeg = placeholder
            self._latest_ai_jpeg = placeholder
            self._frame_seq += 1

        while self._running:
            # Smart Idle Check: nếu không có ai xem trong 10s -> tạm dừng
            with self._lock:
                idle = self._viewer_count <= 0 and (time.time() - self._last_viewer_time > 10.0)
                cam_target = self.cam_url

            if idle:
                log.info("Không có người xem trong 10s. Dừng luồng StreamRelay để tiết kiệm tài nguyên.")
                with self._lock:
                    self._running = False
                break

            try:
                req = urllib.request.Request(
                    cam_target,
                    headers={"User-Agent": "PlantcareStreamRelay/1.0"},
                )
                with urllib.request.urlopen(req, timeout=10.0) as stream:
                    log.info("Đã kết nối thành công tới camera stream: %s", cam_target)
                    buffer = b""
                    while self._running:
                        with self._lock:
                            if self._viewer_count <= 0 and (time.time() - self._last_viewer_time > 10.0):
                                break
                            if self.cam_url != cam_target:
                                break  # URL đã đổi, reconnect tới URL mới

                        chunk = stream.read(4096)
                        if not chunk:
                            log.info("Luồng stream từ %s đã kết thúc hoặc đóng bởi camera.", cam_target)
                            break
                        buffer += chunk

                        # Trích xuất toàn bộ JPEG trong buffer, giữ duy nhất frame mới nhất (Zero-Lag Drop)
                        last_jpeg = None
                        while True:
                            start = buffer.find(b"\xff\xd8")
                            if start == -1:
                                if len(buffer) > 65536:
                                    buffer = buffer[-4096:]
                                break
                            end = buffer.find(b"\xff\xd9", start + 2)
                            if end == -1:
                                if start > 0:
                                    buffer = buffer[start:]
                                break
                            # Tìm thấy một frame hoàn chỉnh
                            last_jpeg = buffer[start : end + 2]
                            buffer = buffer[end + 2 :]

                        if last_jpeg:
                            with self._lock:
                                self._latest_raw_jpeg = last_jpeg
                                self._latest_frame_time = time.time()
                                self._frame_seq += 1

                    time.sleep(0.5)

            except urllib.error.HTTPError as exc:
                if exc.code == 409:
                    log.info("ESP32-CAM đang ở trạng thái TẮT livestream (409 Conflict). Chờ người dùng bật...")
                    standby_frame = self._create_status_frame("Livestream dang TAT. Hay nhan 'Bat Livestream' tren Dashboard.")
                    with self._lock:
                        self._latest_raw_jpeg = standby_frame
                        self._latest_ai_jpeg = standby_frame
                        self._frame_seq += 1
                else:
                    log.warning("Lỗi HTTP từ %s (%s). Thử lại sau 2s...", cam_target, exc)
                time.sleep(2.0)
            except Exception as exc:
                log.warning("Không thể đọc stream từ %s (%s). Thử lại sau 2s...", cam_target, exc)
                reconnect_frame = self._create_status_frame("ESP32-CAM chua bat stream hoac mat ket noi. Dang thu lai...")
                with self._lock:
                    self._latest_raw_jpeg = reconnect_frame
                    self._latest_ai_jpeg = reconnect_frame
                    self._frame_seq += 1
                time.sleep(2.0)

    def _render_overlay(self, frame_data: bytes, detections: list[dict], leaf_info: dict | None) -> bytes:
        """Vẽ Bounding Box phục vụ fallback cho HTTP Multipart stream."""
        if not detections and not leaf_info:
            return frame_data
        try:
            np_arr = np.frombuffer(frame_data, np.uint8)
            img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if img is None:
                return frame_data
            h, w = img.shape[:2]

            for det in detections:
                bbox = det.get("bbox_xyxy_normalized")
                if not bbox or len(bbox) != 4:
                    continue
                x1 = max(0, min(w - 1, int(bbox[0] * w)))
                y1 = max(0, min(h - 1, int(bbox[1] * h)))
                x2 = max(0, min(w - 1, int(bbox[2] * w)))
                y2 = max(0, min(h - 1, int(bbox[3] * h)))

                is_ripe = det.get("class_name") == "ripe" or "Chín" in str(det.get("label", ""))
                color = (40, 40, 230) if is_ripe else (34, 197, 94)

                cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)

                raw_lbl = f"{det.get('label', 'Qua')} {int((det.get('confidence') or 0.8) * 100)}%"
                lbl = _to_ascii(raw_lbl)
                (tw, th), _ = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
                pill_y1 = max(0, y1 - th - 6)
                cv2.rectangle(img, (x1, pill_y1), (x1 + tw + 6, y1), color, -1)
                cv2.putText(
                    img,
                    lbl,
                    (x1 + 3, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.48,
                    (255, 255, 255),
                    1,
                    cv2.LINE_AA,
                )

            banner_text = ""
            if leaf_info:
                conf_pct = int((leaf_info.get("confidence") or 0.0) * 100)
                banner_text = _to_ascii(
                    f"La: {leaf_info['label']} ({conf_pct}%) | Phat hien: {len(detections)} qua"
                )
            elif detections:
                banner_text = f"Phat hien: {len(detections)} qua"

            if banner_text:
                cv2.rectangle(img, (0, 0), (w, 32), (15, 23, 42), -1)
                cv2.putText(
                    img,
                    banner_text,
                    (12, 22),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.52,
                    (56, 189, 248),
                    1,
                    cv2.LINE_AA,
                )

            _, encoded_buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            return encoded_buf.tobytes()
        except Exception as exc:
            log.debug("Lỗi khi vẽ overlay OpenCV: %s", exc)
            return frame_data

    def _ai_loop(self) -> None:
        """Luồng 2: Suy luận AI không đồng bộ (YOLOv8 + MobileNetV3) ~3-5 FPS."""
        last_processed_time = 0.0

        while self._running:
            with self._lock:
                frame_data = self._latest_raw_jpeg
                frame_time = self._latest_frame_time

            if not frame_data or frame_time == last_processed_time:
                time.sleep(0.04)
                continue

            last_processed_time = frame_time

            # Chạy mô hình dự đoán (bỏ qua frame placeholder nhỏ)
            if len(frame_data) < 3000:
                time.sleep(0.05)
                continue

            try:
                fruit_res = predict("fruit", {}, image_bytes=frame_data)
                detections = fruit_res.get("detections", [])

                leaf_res = predict("leaf", {}, image_bytes=frame_data)
                leaf_info = {
                    "label": leaf_res.get("label", "Không rõ"),
                    "confidence": leaf_res.get("confidence", 0.0),
                }
                with self._lock:
                    self._active_detections = detections
                    self._active_leaf_info = leaf_info
                    self._latest_ai_time = time.time()
                    # Cập nhật thêm fallback overlay cho HTTP viewer nếu có
                    if self._latest_raw_jpeg:
                        self._latest_ai_jpeg = self._render_overlay(
                            self._latest_raw_jpeg, detections, leaf_info
                        )
            except Exception as exc:
                log.debug("Lỗi trong chu kỳ inference AI: %s", exc)

            time.sleep(0.18)  # ~4-5 FPS cho chu kỳ suy luận AI

    def get_status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "cam_url": self.cam_url,
                "viewer_count": self._viewer_count,
                "active_fruits_count": len(self._active_detections),
                "leaf_condition": self._active_leaf_info.get("label") if self._active_leaf_info else None,
                "last_frame_age_seconds": round(time.time() - self._latest_frame_time, 2)
                if self._latest_frame_time > 0
                else None,
            }

    def get_latest_frame(self) -> tuple[bytes | None, int, float]:
        """Lấy frame JPEG thô mới nhất, số sequence và thời điểm nhận (dùng cho WebSocket Binary)."""
        with self._lock:
            return self._latest_raw_jpeg, self._frame_seq, self._latest_frame_time

    def get_latest_ai_metadata(self) -> tuple[list[dict], dict | None, float]:
        """Lấy siêu dữ liệu nhận diện AI mới nhất (dùng cho WebSocket JSON overlay)."""
        with self._lock:
            dets = [dict(d) for d in self._active_detections]
            leaf = dict(self._active_leaf_info) if self._active_leaf_info else None
            return dets, leaf, self._latest_ai_time

    def extract_snapshot(self) -> bytes | None:
        """Trích xuất 1 khung hình JPEG mới nhất từ luồng stream cho tác vụ AI định kỳ 30 phút."""
        with self._lock:
            if self._latest_raw_jpeg and len(self._latest_raw_jpeg) > 3000:
                return bytes(self._latest_raw_jpeg)
            return None

    def generate_stream(self, mode: str = "ai", max_frames: int | None = None) -> Generator[bytes, None, None]:
        """Fallback: Tạo luồng generator phát HTTP multipart (raw hoặc ai) ở tốc độ ~20 FPS."""
        self.add_viewer()
        try:
            boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
            trailer = b"\r\n"
            yielded = 0
            while True:
                with self._lock:
                    if mode == "raw":
                        frame = self._latest_raw_jpeg
                    else:
                        frame = self._latest_ai_jpeg or self._latest_raw_jpeg

                if frame:
                    yield boundary + frame + trailer
                    yielded += 1
                    if max_frames is not None and yielded >= max_frames:
                        break
                time.sleep(0.05)  # ~20 FPS
        finally:
            self.remove_viewer()


# Singleton instance toàn hệ thống
stream_relay = StreamRelay()
