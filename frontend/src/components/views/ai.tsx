"use client";
import { usePlantcare } from "@/components/plantcare-context";
import { Icon, ProtectedImage } from "@/components/dashboard";
import Link from "next/link";
import { AiHistory } from "@/components/ai-history";
import { Job, request, statusLabel } from "@/lib/api";
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
  return (
    <>
      <section className="ai-intro">
        <h2>Ba góc nhìn. Một cây cà chua.</h2>
        <p className="muted">
          XGBoost cho thời tiết, MobileNetV3 cho lá, YOLO cho quả. Worker chạy
          model đã huấn luyện trên CPU. Chưa đánh giá ảnh ESP32-CAM.
        </p>
      </section>
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
