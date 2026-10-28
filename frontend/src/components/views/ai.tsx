"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { usePlantcare } from "@/components/plantcare-context";
import { Icon, ProtectedImage } from "@/components/dashboard";
import Link from "next/link";
import { AiHistory } from "@/components/ai-history";
import { Job, request, statusLabel, API } from "@/lib/api";

type CamStatus = {
  online: boolean;
  ip: string;
  stream_enabled: boolean;
  stream_url: string;
  interval_seconds: number;
  next_capture_in_seconds: number;
  upload_count: number;
  last_upload_ok: boolean;
  last_leaf_status: number;
  last_fruit_status: number;
  capture_in_progress: boolean;
};

type AiOverlayMessage = {
  type: string;
  leaf?: { label: string; confidence: number } | null;
  fruits?: Array<{
    class_name?: string;
    label?: string;
    confidence?: number;
    bbox_xyxy_normalized?: [number, number, number, number];
  }>;
  fps?: number;
  timestamp?: number;
};

const CAM_IP_STORAGE_KEY = "plantcare_esp32_cam_ip";
const DEFAULT_CAM_IP = "10.213.128.82";

function formatCountdown(sec: number | undefined): string {
  if (sec === undefined || sec < 0) return "--:--";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function AiView() {
  const {
    data,
    apiOnline,
    busy,
    act,
    token,
    runWeather,
    purpose,
    setPurpose,
    file,
    setFile,
    fileInput,
    image,
    setSelectedJob,
  } = usePlantcare();

  const [camIp, setCamIp] = useState<string>(() => {
    if (typeof window !== "undefined") {
      return window.localStorage.getItem(CAM_IP_STORAGE_KEY) || DEFAULT_CAM_IP;
    }
    return DEFAULT_CAM_IP;
  });
  const [camStatus, setCamStatus] = useState<CamStatus | null>(null);
  const [camOnline, setCamOnline] = useState<boolean>(false);
  const [camBusy, setCamBusy] = useState<boolean>(false);
  const [streamNonce, setStreamNonce] = useState<number>(0);
  const [streamMode, setStreamMode] = useState<"ai" | "raw">("ai");
  const [streamStats, setStreamStats] = useState<{
    running?: boolean;
    active_fruits_count?: number;
    leaf_condition?: string | null;
  } | null>(null);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const pendingBitmapRef = useRef<ImageBitmap | null>(null);
  const lastDrawnBitmapRef = useRef<ImageBitmap | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const latestAiOverlayRef = useRef<AiOverlayMessage | null>(null);
  const pollFailuresRef = useRef<number>(0);

  const cleanCamIp = camIp.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "");

  useEffect(() => {
    if (typeof window !== "undefined" && cleanCamIp) {
      window.localStorage.setItem(CAM_IP_STORAGE_KEY, cleanCamIp);
    }
  }, [cleanCamIp]);

  const drawFrameAndOverlay = useCallback(
    (ctx: CanvasRenderingContext2D, width: number, height: number) => {
      if (streamMode !== "ai") return;
      const overlay = latestAiOverlayRef.current;
      if (!overlay) return;

      // 1. Dải banner thông tin lá ở phía trên canvas
      if (overlay.leaf?.label) {
        const conf = Math.round((overlay.leaf.confidence || 0) * 100);
        const fruitsCount = overlay.fruits?.length || 0;
        const bannerText = `Lá: ${overlay.leaf.label} (${conf}%) · Phát hiện: ${fruitsCount} quả`;
        ctx.fillStyle = "rgba(15, 23, 42, 0.85)";
        ctx.fillRect(0, 0, width, 28);
        ctx.fillStyle = "#38bdf8";
        ctx.font = "bold 13px system-ui, -apple-system, sans-serif";
        ctx.textBaseline = "middle";
        ctx.fillText(bannerText, 12, 14);
      }

      // 2. Vẽ Bounding Box quả cà chua
      if (Array.isArray(overlay.fruits)) {
        for (const fruit of overlay.fruits) {
          const bbox = fruit.bbox_xyxy_normalized;
          if (!bbox || bbox.length !== 4) continue;
          const x1 = bbox[0] * width;
          const y1 = bbox[1] * height;
          const bw = (bbox[2] - bbox[0]) * width;
          const bh = (bbox[3] - bbox[1]) * height;

          const isRipe =
            fruit.class_name === "ripe" ||
            (fruit.label && fruit.label.includes("Chín"));
          const strokeColor = isRipe ? "#ef4444" : "#22c55e";
          const badgeText = `${fruit.label || (isRipe ? "Chín" : "Xanh")} ${Math.round((fruit.confidence || 0.8) * 100)}%`;

          ctx.lineWidth = 2.5;
          ctx.strokeStyle = strokeColor;
          ctx.strokeRect(x1, y1, bw, bh);

          ctx.font = "bold 12px system-ui, -apple-system, sans-serif";
          const tw = ctx.measureText(badgeText).width;
          const pillH = 18;
          const pillW = tw + 10;
          const pillY = Math.max(0, y1 - pillH - 2);

          ctx.fillStyle = strokeColor;
          ctx.fillRect(x1, pillY, pillW, pillH);

          ctx.fillStyle = "#ffffff";
          ctx.textBaseline = "middle";
          ctx.fillText(badgeText, x1 + 5, pillY + pillH / 2);
        }
      }
    },
    [streamMode]
  );

  useEffect(() => {
    if (!cleanCamIp) {
      setCamOnline(false);
      setCamStatus(null);
      return;
    }
    let cancelled = false;
    const pollCam = async () => {
      try {
        const json = await request<CamStatus>(
          `/stream/cam-status?cam_ip=${encodeURIComponent(cleanCamIp)}`,
          token,
        );
        if (!cancelled && json) {
          pollFailuresRef.current = 0;
          setCamStatus(json);
          setCamOnline(Boolean(json.online !== false && json.ip));
        }
      } catch {
        if (!cancelled) {
          pollFailuresRef.current += 1;
          // Chỉ tắt camOnline nếu thất bại 3 lần liên tiếp (tránh unmount video khi Wi-Fi bận)
          if (pollFailuresRef.current >= 3) {
            setCamOnline(false);
          }
        }
      }
    };
    void pollCam();
    const timer = window.setInterval(pollCam, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [cleanCamIp, token]);

  const toggleStream = async (enable: boolean) => {
    if (!cleanCamIp) return;
    setCamBusy(true);
    try {
      const res = await request<{ ok: boolean; cam_status: CamStatus }>(
        `/stream/toggle?enable=${enable}&cam_ip=${encodeURIComponent(cleanCamIp)}`,
        token,
        { method: "POST" },
      );
      setStreamNonce(Date.now());
      if (res?.cam_status) {
        setCamStatus(res.cam_status);
        setCamOnline(true);
      }
    } finally {
      setCamBusy(false);
    }
  };

  // WebSocket Live Stream Client: nhận Binary JPEG và JSON AI overlay
  useEffect(() => {
    if (!camOnline || !camStatus?.stream_enabled || !token) {
      return;
    }

    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    let host = window.location.hostname + ":8000";
    try {
      if (API.startsWith("http")) {
        const parsed = new URL(API);
        host = parsed.host;
      }
    } catch {}

    const wsUrl = `${wsProtocol}//${host}/ws/stream?token=${encodeURIComponent(token)}&cam_url=http://${cleanCamIp}:8081/stream`;
    let ws: WebSocket | null = null;
    let reconnectTimer: NodeJS.Timeout | null = null;
    let isCancelled = false;

    // Vòng lặp render GPU 60 FPS đồng bộ tuyệt đối giữa video và bounding box
    const renderLoop = () => {
      if (isCancelled) return;
      const canvas = canvasRef.current;
      const nextBmp = pendingBitmapRef.current;

      if (canvas && nextBmp) {
        pendingBitmapRef.current = null;
        if (canvas.width !== nextBmp.width || canvas.height !== nextBmp.height) {
          canvas.width = nextBmp.width;
          canvas.height = nextBmp.height;
        }
        const ctx = canvas.getContext("2d", { alpha: false });
        if (ctx) {
          ctx.drawImage(nextBmp, 0, 0);
          drawFrameAndOverlay(ctx, canvas.width, canvas.height);
          if (lastDrawnBitmapRef.current) {
            lastDrawnBitmapRef.current.close();
          }
          lastDrawnBitmapRef.current = nextBmp;
        } else {
          nextBmp.close();
        }
      }

      animFrameRef.current = requestAnimationFrame(renderLoop);
    };

    animFrameRef.current = requestAnimationFrame(renderLoop);

    const connect = () => {
      if (isCancelled) return;
      try {
        ws = new WebSocket(wsUrl);
        ws.binaryType = "blob";

        ws.onmessage = (event) => {
          if (isCancelled) return;
          if (event.data instanceof Blob) {
            createImageBitmap(event.data)
              .then((bmp) => {
                if (isCancelled) {
                  bmp.close();
                  return;
                }
                // Nếu frame cũ chưa kịp vẽ trước v-sync tiếp theo, giải phóng ngay để triệt tiêu độ trễ
                if (pendingBitmapRef.current) {
                  pendingBitmapRef.current.close();
                }
                pendingBitmapRef.current = bmp;
              })
              .catch(() => {});
          } else if (typeof event.data === "string") {
            try {
              const msg = JSON.parse(event.data) as AiOverlayMessage;
              if (msg.type === "ai_overlay") {
                latestAiOverlayRef.current = msg;
                setStreamStats((prev) => {
                  const nFruits = msg.fruits?.length ?? 0;
                  const nLeaf = msg.leaf?.label ?? null;
                  if (
                    prev?.running &&
                    prev.active_fruits_count === nFruits &&
                    prev.leaf_condition === nLeaf
                  ) {
                    return prev;
                  }
                  return {
                    running: true,
                    active_fruits_count: nFruits,
                    leaf_condition: nLeaf,
                  };
                });
                // Cập nhật ngay overlay trên khung hình hiện tại nếu không có frame mới
                const canvas = canvasRef.current;
                const lastBmp = lastDrawnBitmapRef.current;
                if (canvas && lastBmp) {
                  const ctx = canvas.getContext("2d", { alpha: false });
                  if (ctx) {
                    ctx.drawImage(lastBmp, 0, 0);
                    drawFrameAndOverlay(ctx, canvas.width, canvas.height);
                  }
                }
              }
            } catch {}
          }
        };

        ws.onclose = () => {
          if (!isCancelled) {
            reconnectTimer = setTimeout(connect, 2000);
          }
        };

        ws.onerror = () => {
          try {
            ws?.close();
          } catch {}
        };
      } catch {
        if (!isCancelled) {
          reconnectTimer = setTimeout(connect, 2000);
        }
      }
    };

    connect();

    return () => {
      isCancelled = true;
      if (animFrameRef.current) {
        cancelAnimationFrame(animFrameRef.current);
        animFrameRef.current = null;
      }
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) {
        try {
          ws.close();
        } catch {}
      }
      if (pendingBitmapRef.current) {
        pendingBitmapRef.current.close();
        pendingBitmapRef.current = null;
      }
      if (lastDrawnBitmapRef.current) {
        lastDrawnBitmapRef.current.close();
        lastDrawnBitmapRef.current = null;
      }
    };
  }, [camOnline, camStatus?.stream_enabled, streamMode, token, cleanCamIp, drawFrameAndOverlay]);

  return (
    <>
      <section className="ai-intro">
        <h2>Ba góc nhìn. Một cây cà chua.</h2>
        <p className="muted">
          XGBoost cho thời tiết, MobileNetV3 cho lá, YOLO cho quả. Hỗ trợ
          ESP32-CAM 2 chế độ: Livestream bật/tắt theo yêu cầu & chụp tự động
          30 phút/lần gửi đồng thời cả 2 tác vụ AI (Lá + Quả).
        </p>
      </section>
      <article className="card" style={{ marginBottom: "1.25rem" }}>
        <div className="section-heading">
          <div>
            <span className="eyebrow">CAMERA VƯỜN · AI-THINKER OV2640</span>
            <h3>ESP32-CAM · Livestream & Chụp Tự Động (30 phút/lần)</h3>
          </div>
          <span className={`badge ${camOnline ? "ok" : "mock"}`}>
            {camOnline
              ? `TRỰC TUYẾN · ${camStatus?.ip ?? cleanCamIp}`
              : "CHƯA KẾT NỐI CAM"}
          </span>
        </div>
        <div
          style={{
            display: "flex",
            gap: "0.75rem",
            flexWrap: "wrap",
            alignItems: "center",
            marginTop: "0.75rem",
          }}
        >
          <label className="small muted" htmlFor="esp32-cam-ip-input">
            IP ESP32-CAM (Wi-Fi LAN):
          </label>
          <input
            id="esp32-cam-ip-input"
            type="text"
            placeholder="Ví dụ: 10.213.128.82"
            value={camIp}
            onChange={(e) => setCamIp(e.target.value)}
            style={{
              padding: "0.45rem 0.75rem",
              borderRadius: "8px",
              border: "1px solid var(--line, #cbd5e1)",
              minWidth: "200px",
              fontFamily: "monospace",
            }}
          />
          {cleanCamIp && (
            <a
              href={`http://${cleanCamIp}/`}
              target="_blank"
              rel="noreferrer"
              className="text-button"
            >
              Mở trang ESP32-CAM ↗
            </a>
          )}
        </div>

        <div
          className="two-columns"
          style={{ marginTop: "1rem", alignItems: "start" }}
        >
          <div>
            <h4>Chế độ 1 · Xem Livestream (Bật/Tắt khi cần)</h4>
            <p className="small muted">
              Chỉ phát MJPEG qua cổng 81 khi bật để tiết kiệm điện và giữ mát
              cảm biến OV2640.
            </p>
            <div
              style={{
                display: "flex",
                gap: "0.5rem",
                marginTop: "0.5rem",
                marginBottom: "0.75rem",
              }}
            >
              <button
                className="button"
                disabled={
                  !camOnline || camBusy || Boolean(camStatus?.stream_enabled)
                }
                onClick={() => void toggleStream(true)}
              >
                Bật Livestream
              </button>
              <button
                className="button secondary"
                disabled={!camOnline || camBusy || !camStatus?.stream_enabled}
                onClick={() => void toggleStream(false)}
              >
                Tắt Livestream
              </button>
            </div>
            {camOnline && camStatus?.stream_enabled ? (
              <div
                style={{
                  borderRadius: "12px",
                  overflow: "hidden",
                  border: "1px solid var(--line, #cbd5e1)",
                  background: "#000",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    gap: "0.35rem",
                    padding: "0.45rem",
                    background: "#1e293b",
                    flexWrap: "wrap",
                  }}
                >
                  <button
                    type="button"
                    className={`button small ${streamMode === "ai" ? "" : "secondary"}`}
                    style={{ fontSize: "0.78rem", padding: "0.25rem 0.55rem" }}
                    onClick={() => setStreamMode("ai")}
                  >
                    ⚡ AI Realtime (YOLO + Lá)
                  </button>
                  <button
                    type="button"
                    className={`button small ${streamMode === "raw" ? "" : "secondary"}`}
                    style={{ fontSize: "0.78rem", padding: "0.25rem 0.55rem" }}
                    onClick={() => setStreamMode("raw")}
                  >
                    Mượt qua Relay (Raw)
                  </button>
                </div>
                <div
                  style={{
                    position: "relative",
                    width: "100%",
                    maxHeight: "400px",
                    overflow: "hidden",
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    background: "#020617",
                    borderRadius: "0 0 12px 12px",
                  }}
                >
                  {/* Trực tiếp render bằng GPU qua Canvas & createImageBitmap 60 FPS */}
                  <canvas
                    ref={canvasRef}
                    width={640}
                    height={480}
                    style={{
                      width: "100%",
                      maxHeight: "400px",
                      objectFit: "contain",
                      display: "block",
                      aspectRatio: "640 / 480",
                    }}
                  />
                </div>
                {streamMode === "ai" && (
                  <div
                    style={{
                      padding: "0.35rem 0.75rem",
                      background: "#0f172a",
                      color: "#38bdf8",
                      fontSize: "0.75rem",
                      display: "flex",
                      justifyContent: "space-between",
                      flexWrap: "wrap",
                      gap: "0.5rem",
                    }}
                  >
                    <span>
                      🔍 Phát hiện: <strong>{streamStats?.active_fruits_count ?? 0} quả</strong>
                    </span>
                    <span>
                      Lá: <strong>{streamStats?.leaf_condition ?? "Đang nhận diện..."}</strong>
                    </span>
                  </div>
                )}
              </div>
            ) : (
              <div className="empty" style={{ padding: "1.25rem" }}>
                <Icon name="image" size={28} />
                <p className="small muted">
                  {camOnline
                    ? "Livestream đang TẮT. Nhấn “Bật Livestream” khi bạn muốn quan sát trực tiếp."
                    : "Nhập địa chỉ IP của ESP32-CAM để kết nối và điều khiển."}
                </p>
              </div>
            )}
          </div>

          <div>
            <h4>Chế độ 2 · Chụp định kỳ 30 phút/lần (Lá + Quả)</h4>
            <p className="small muted">
              Mỗi 30 phút ESP32-CAM tự động chụp 1 ảnh JPEG mới và gửi đồng thời
              tới cả 2 model AI: <strong>MobileNetV3 (Bệnh lá)</strong> và{" "}
              <strong>YOLOv8 (Độ chín quả)</strong>.
            </p>
            <div
              style={{
                marginTop: "0.75rem",
                padding: "0.85rem",
                borderRadius: "10px",
                background: "rgba(15, 23, 42, 0.04)",
              }}
            >
              <p className="small" style={{ margin: "0 0 0.35rem 0" }}>
                ⏱ Lần chụp tự động tiếp theo sau:{" "}
                <strong>
                  {formatCountdown(camStatus?.next_capture_in_seconds)}
                </strong>{" "}
                (chu kỳ 30 phút)
              </p>
              <p className="small muted" style={{ margin: "0 0 0.35rem 0" }}>
                Tổng số tác vụ AI đã gửi:{" "}
                <strong>{camStatus?.upload_count ?? 0}</strong> · Trạng thái:{" "}
                <strong>
                  {camStatus?.capture_in_progress
                    ? "Đang chụp & tải ảnh..."
                    : "Sẵn sàng"}
                </strong>
              </p>
              <p className="small muted" style={{ margin: 0 }}>
                Kết quả gửi gần nhất: Lá (HTTP{" "}
                <code>{camStatus?.last_leaf_status ?? 0}</code>) · Quả (HTTP{" "}
                <code>{camStatus?.last_fruit_status ?? 0}</code>)
              </p>
            </div>
            <button
              className="button full"
              style={{ marginTop: "0.75rem" }}
              disabled={
                !camOnline ||
                camBusy ||
                busy ||
                Boolean(camStatus?.capture_in_progress)
              }
              onClick={() =>
                act(async () => {
                  setCamBusy(true);
                  try {
                    if (camStatus?.stream_enabled) {
                      // Khi Livestream đang bật: chụp trực tiếp từ luồng stream ở Backend (Zero-Lag, không gián đoạn video)
                      const res = await request<{
                        leaf_job?: Job;
                        fruit_job?: Job;
                      }>("/stream/capture?purpose=both", token, {
                        method: "POST",
                      });
                      if (res?.fruit_job?.id) {
                        setSelectedJob(res.fruit_job.id);
                      }
                    } else {
                      // Khi Livestream đang tắt: gửi yêu cầu ESP32-CAM chụp và tải lên qua Backend proxy
                      await request<{ ok: boolean; cam_status: CamStatus }>(
                        `/stream/capture-esp32?cam_ip=${encodeURIComponent(cleanCamIp)}`,
                        token,
                        { method: "POST" },
                      );
                    }
                  } finally {
                    setCamBusy(false);
                  }
                }, "Đã chụp và gửi đồng thời 2 tác vụ AI (Bệnh lá + Độ chín quả)!")
              }
            >
              Chụp & Phân tích cả Lá + Quả ngay →
            </button>
          </div>
        </div>
      </article>
      <section className="two-columns ai-columns">
        <article className="card">
          <h3>Thêm ảnh để phân tích</h3>
          <p className="small muted">
            JPEG hoặc PNG · tối đa 5 MiB · 16 megapixel
          </p>
          <div className="segmented">
            <button
              className={purpose === "leaf" ? "selected" : ""}
              aria-pressed={purpose === "leaf"}
              onClick={() => setPurpose("leaf")}
            >
              Bệnh lá · MobileNetV3
            </button>
            <button
              className={purpose === "fruit" ? "selected" : ""}
              aria-pressed={purpose === "fruit"}
              onClick={() => setPurpose("fruit")}
            >
              Độ chín · YOLO
            </button>
          </div>
          <label className="upload-zone">
            <Icon name="upload" size={32} />
            <strong>{file?.name ?? "Chọn một bức ảnh của cây"}</strong>
            <span>Ảnh được lưu private trong MinIO</span>
            <input
              ref={fileInput}
              type="file"
              accept="image/jpeg,image/png"
              aria-label="Chọn ảnh cây"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <button
            className="button full"
            disabled={!file || busy || !apiOnline}
            onClick={() =>
              act(async () => {
                if (!file) return;
                if (file.size > 5 * 1024 * 1024)
                  throw new Error("Ảnh tối đa 5 MiB");
                const form = new FormData();
                form.append("file", file);
                const job = await request<Job>(
                  `/images?purpose=${purpose}`,
                  token,
                  { method: "POST", body: form },
                );
                setSelectedJob(job.id);
                setFile(null);
                if (fileInput.current) fileInput.current.value = "";
              }, "Ảnh đã lưu. Worker đang chạy model đã huấn luyện.")
            }
          >
            Tải ảnh & chạy AI →
          </button>
          <div className="weather-card">
            <h3>Dự báo mưa · XGBoost</h3>
            <p className="small muted">{data.weather_status.reason}</p>
            <button
              className="text-button"
              disabled={busy || !apiOnline || !data.weather_status.available}
              onClick={runWeather}
            >
              Chạy dự báo mưa →
            </button>
            <p className="small muted">
              Nguồn thời tiết dùng cho tưới:{" "}
              {data.weather_control.mode === "demo"
                ? "DEMO · kịch bản mô phỏng, không phải dự báo AI"
                : "LIVE · XGBoost / OpenWeather"}
              .
            </p>
            <Link href="/schedule" className="text-button">
              Cấu hình nguồn thời tiết ↗
            </Link>
          </div>
        </article>
        <article className="card result-card">
          <div className="section-heading">
            <h3>Kết quả phân tích</h3>
            <span className="badge mock">
              {image?.provider === "mock" ? "MOCK CŨ" : "MODEL ĐÃ HUẤN LUYỆN"}
            </span>
          </div>
          {image ? (
            <>
              <ProtectedImage key={image.image_id} job={image} token={token} />
              <div className="result-line">
                <div>
                  <span className="eyebrow">
                    {image.task === "leaf" ? "MOBILENETV3" : "YOLO"} ·
                    {image.model_version}
                  </span>
                  <h2>{image.result?.label ?? statusLabel[image.status]}</h2>
                </div>
                {image.result?.confidence && (
                  <strong className="confidence">
                    {Math.round(image.result.confidence * 100)}%
                    <small>điểm mô hình</small>
                  </strong>
                )}
              </div>
              <p className="small muted">
                {image.result?.notice ?? "Job đã lưu; đang chờ worker xử lý."}
              </p>
              {image.error && <p className="error small">{image.error}</p>}
              {image.status === "failed" && image.provider === "trained" && (
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() =>
                    act(
                      () =>
                        request(`/jobs/${image.id}/retry`, token, {
                          method: "POST",
                        }),
                      "Đã đưa tác vụ vào hàng đợi lại.",
                    )
                  }
                >
                  Thử lại
                </button>
              )}
            </>
          ) : (
            <div className="empty image-empty">
              <Icon name="image" size={42} />
              <h3>Mỗi chiếc lá có một câu chuyện</h3>
              <p>Tải ảnh đầu tiên để kiểm thử luồng AI.</p>
            </div>
          )}
        </article>
      </section>
      <AiHistory
        jobs={data.jobs}
        selectedId={image?.id}
        select={setSelectedJob}
      />
    </>
  );
}
