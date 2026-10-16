"use client";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/app-shell";
import { WS, Dashboard, request } from "@/lib/api";

import { Icon, PlantArt } from "@/components/dashboard";
import { PlantcareContext } from "@/components/plantcare-context";
import { ActionFeedback } from "@/components/action-feedback";

export default function Home({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState("");
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [feedbackId, setFeedbackId] = useState(0);
  const dismissNotice = useCallback(() => setNotice(""), []);
  const dismissError = useCallback(() => setError(""), []);
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [apiOnline, setApiOnline] = useState(false);
  const [purpose, setPurpose] = useState("leaf");
  const [file, setFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [duration, setDuration] = useState(5);
  const [selectedJob, setSelectedJob] = useState<string | null>(null);
  const snapshotRevision = useRef(0);
  const acceptSnapshot = useCallback((incoming: Dashboard) => {
    snapshotRevision.current += 1;
    setData((current) =>
      current &&
      Date.parse(current.server_time) > Date.parse(incoming.server_time)
        ? current
        : incoming,
    );
  }, []);
  const refresh = useCallback(async () => {
    const revision = snapshotRevision.current;
    try {
      acceptSnapshot(await request<Dashboard>("/dashboard", token));
      setApiOnline(true);
    } catch {
      // Một REST cũ thất bại không được ghi đè kết nối WebSocket vừa nhận dữ liệu.
      if (revision === snapshotRevision.current) setApiOnline(false);
    }
  }, [token, acceptSnapshot]);
  useEffect(() => {
    if (!token) return;
    let disposed = false;
    let socket: WebSocket;
    let retry: ReturnType<typeof setTimeout>;
    function connect() {
      socket = new WebSocket(WS);
      socket.onopen = () => socket.send(JSON.stringify({ token }));
      socket.onmessage = (e) => {
        if (disposed) return;
        const packet = JSON.parse(e.data);
        if (packet.type === "snapshot") {
          acceptSnapshot(packet.data);
          setConnected(true);
          setApiOnline(true);
        }
        if (packet.type === "service_unavailable") setApiOnline(false);
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        if (!disposed) {
          setConnected(false);
          retry = setTimeout(connect, 3000);
        }
      };
    }
    connect();
    const poll = setInterval(refresh, 8000);
    return () => {
      disposed = true;
      clearTimeout(retry);
      clearInterval(poll);
      socket?.close();
    };
  }, [token, refresh, acceptSnapshot]);
  const act = async (action: () => Promise<unknown>, success: string) => {
    setFeedbackId((id) => id + 1);
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(success);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Có lỗi xảy ra");
    } finally {
      setBusy(false);
    }
  };
  async function login(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const key = String(new FormData(e.currentTarget).get("token"));
    e.currentTarget.reset();
    try {
      setData(await request<Dashboard>("/dashboard", key));
      setToken(key);
      setApiOnline(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không kết nối được backend");
    } finally {
      setBusy(false);
    }
  }
  if (!token || !data)
    return (
      <main className="login-page">
        <div className="login-story">
          <div className="brand">
            <Icon name="leaf" size={32} />
            plantcare <span>/ AIoT</span>
          </div>
          <h1>
            Chăm một mầm xanh.
            <br />
            Hiểu cả khu vườn.
          </h1>
          <p>
            Không gian theo dõi cây cà chua, điều khiển tưới và khám phá AI
            trong một luồng kết nối.
          </p>
          <PlantArt />
          <span className="eyebrow">ĐỒ ÁN AIoT · SIMULATION LAB</span>
        </div>
        <form className="login-card" onSubmit={login}>
          <span className="badge mock">DEMO · AI ĐÃ HUẤN LUYỆN</span>
          <h2>Vào khu vườn của bạn</h2>
          <p className="muted">
            Nhập khóa truy cập của môi trường demo để mở dashboard.
          </p>
          <label>
            Khóa truy cập
            <input
              name="token"
              type="password"
              autoComplete="current-password"
              placeholder="APP_TOKEN trong file .env"
              required
              minLength={16}
            />
          </label>
          <button className="button full" disabled={busy}>
            {busy ? "Đang kết nối…" : "Mở dashboard →"}
          </button>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <p className="small muted">
            Thiết bị mô phỏng · Dữ liệu được lưu thật
            <br />
            Không điều khiển bơm vật lý.
          </p>
        </form>
      </main>
    );
  const images = data.jobs.filter((j) => j.image_id);
  const image = images.find((j) => j.id === selectedJob) ?? images[0];
  const stale = data.stale || !apiOnline;
  const pumpOn = data.device.pump === "on" && apiOnline;
  const runWeather = () =>
    act(
      () => request("/ai/weather", token, { method: "POST" }),
      "Đã đưa dự báo mưa vào hàng đợi AI.",
    );
  const pump = (action: string) =>
    act(
      () =>
        request("/irrigation/commands", token, {
          method: "POST",
          body: JSON.stringify({
            command_id: crypto.randomUUID(),
            action,
            duration_seconds: action === "pump_off" ? 1 : duration,
          }),
        }),
      "Đã gửi lệnh; đang chờ ACK thiết bị.",
    );
  return (
    <PlantcareContext.Provider
      value={{
        data,
        token,
        apiOnline,
        busy,
        stale,
        pumpOn,
        duration,
        setDuration,
        pump,
        act,
        runWeather,
        purpose,
        setPurpose,
        file,
        setFile,
        fileInput,
        image,
        setSelectedJob,
      }}
    >
      <AppShell
        data={data}
        connected={connected}
        apiOnline={apiOnline}
        logout={() => {
          setToken("");
          setData(null);
          setConnected(false);
          setError("");
          setNotice("");
        }}
      >
        {error && (
          <ActionFeedback
            key={`error-${feedbackId}`}
            message={error}
            tone="error"
            dismiss={dismissError}
          />
        )}
        {notice && (
          <ActionFeedback
            key={`notice-${feedbackId}`}
            message={notice}
            tone="success"
            dismiss={dismissNotice}
          />
        )}
        {!apiOnline && (
          <div className="message error">
            Backend chưa phản hồi. Số liệu bên dưới là dữ liệu lần cuối nhận
            được.
          </div>
        )}
        {children}
      </AppShell>
    </PlantcareContext.Provider>
  );
}
