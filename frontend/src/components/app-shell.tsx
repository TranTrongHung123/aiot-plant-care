"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Dashboard, statusLabel, time } from "@/lib/api";
import { Icon } from "./dashboard";

type NotifCategory = "all" | "watering" | "image" | "system";

type NotifItem = {
  id: string;
  category: Exclude<NotifCategory, "all">;
  icon: string;
  tone: "good" | "warn" | "info";
  title: string;
  detail: string;
  timestamp: string;
  href: string;
  tag: string;
};

const NOTIF_PAGE_SIZE = 5;

function formatDateTime(value: string) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const timeStr = time(value);
  const dateStr = d.toLocaleDateString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
  return `${timeStr} · ${dateStr}`;
}

function buildNotifications(data: Dashboard): NotifItem[] {
  const items: NotifItem[] = [];

  for (const cmd of data.commands) {
    const secs = Math.max(1, Math.round(cmd.duration_ms / 1000));
    const originText =
      cmd.origin === "automatic" ? "Tự động theo lịch" : "Thủ công";
    const statusText = statusLabel[cmd.status] ?? cmd.status;
    const isOff = cmd.action === "pump_off";
    const isRejected =
      cmd.status === "rejected" ||
      cmd.status === "failed" ||
      cmd.status === "timed_out";
    items.push({
      id: `cmd-${cmd.id}`,
      category: "watering",
      icon: "drop",
      tone: isRejected ? "warn" : "good",
      title: isOff
        ? `Dừng máy bơm (${originText})`
        : `Tưới cây (${originText} · ${secs} giây)`,
      detail: `Tưới lúc ${formatDateTime(cmd.issued_at)} · Trạng thái: ${statusText}${
        cmd.reason ? ` — ${cmd.reason}` : ""
      }`,
      timestamp: cmd.issued_at,
      href: "/history",
      tag: "Tưới cây",
    });
  }

  for (const job of data.jobs) {
    if (job.task === "leaf" || job.task === "fruit" || job.image_id) {
      const isLeaf = job.task === "leaf";
      const modelName = isLeaf ? "MobileNetV3" : "YOLO";
      const statusText = statusLabel[job.status] ?? job.status;
      let resultSummary = `Trạng thái: ${statusText}`;
      if (job.status === "completed" && job.result) {
        if (isLeaf) {
          const conf =
            job.result.confidence !== undefined
              ? ` (${Math.round(job.result.confidence * 100)}%)`
              : "";
          resultSummary = `Chẩn đoán: ${job.result.label}${conf}`;
        } else {
          const detections = job.result.detections ?? [];
          const ripeCount = detections.filter((d) => d.label === "Chín").length;
          const unripeCount = detections.filter(
            (d) => d.label === "Xanh",
          ).length;
          const detailCounts =
            detections.length > 0
              ? ` (${ripeCount} chín, ${unripeCount} xanh)`
              : "";
          resultSummary = `Phát hiện: ${job.result.label}${detailCounts}`;
        }
      } else if (job.error) {
        resultSummary = `Lỗi: ${job.error}`;
      }
      items.push({
        id: `job-img-${job.id}`,
        category: "image",
        icon: "image",
        tone: job.status === "failed" ? "warn" : "info",
        title: `Kết quả ảnh chụp ${isLeaf ? "lá" : "quả"} (${modelName})`,
        detail: `${resultSummary} · Chụp lúc ${formatDateTime(job.created_at)}`,
        timestamp: job.created_at,
        href: "/ai",
        tag: "Ảnh chụp cây",
      });
    } else if (job.task === "weather") {
      const prob =
        job.result?.rain_probability !== undefined
          ? `${Math.round(job.result.rain_probability * 100)}%`
          : "—";
      const label =
        job.result?.label ?? (statusLabel[job.status] ?? job.status);
      items.push({
        id: `job-weather-${job.id}`,
        category: "system",
        icon: "cloud",
        tone: label === "Có mưa" ? "warn" : "info",
        title: `Hệ thống dự báo mưa AI (XGBoost): ${label}`,
        detail: `Xác suất mưa: ${prob} · Cập nhật lúc ${formatDateTime(job.created_at)}`,
        timestamp: job.created_at,
        href: "/schedule",
        tag: "Hệ thống",
      });
    }
  }

  if (data.policy.last_schedule_at) {
    const blocked = data.policy.last_schedule_status !== "requested";
    items.push({
      id: `policy-${data.policy.last_schedule_at}`,
      category: "system",
      icon: "clock",
      tone: blocked ? "warn" : "good",
      title: blocked
        ? "Hệ thống kiểm tra lịch tưới: Chặn / Bỏ qua"
        : "Hệ thống kiểm tra lịch tưới: Đã kích hoạt tưới",
      detail: `${
        data.policy.last_schedule_reason || data.policy.decision
      } · Lúc ${formatDateTime(data.policy.last_schedule_at)}`,
      timestamp: data.policy.last_schedule_at,
      href: "/schedule",
      tag: "Hệ thống",
    });
  }

  if (data.latest) {
    const soil =
      data.latest.readings.soil_moisture_pct !== null
        ? `${Math.round(data.latest.readings.soil_moisture_pct)}%`
        : "Chưa hiệu chuẩn";
    items.push({
      id: `sys-device-${data.device.online ? "on" : "off"}`,
      category: "system",
      icon: "bolt",
      tone: data.device.online ? "good" : "warn",
      title: `Thiết bị ${data.device.name}: ${
        data.device.online ? "Đang trực tuyến" : "Mất kết nối"
      }`,
      detail: `Độ ẩm đất: ${soil} · Nhiệt độ: ${data.latest.readings.temperature_c.toFixed(
        1,
      )}°C · Đo lúc ${formatDateTime(data.latest.measured_at)}`,
      timestamp: data.latest.measured_at,
      href: "/",
      tag: "Hệ thống",
    });
  }

  return items.sort(
    (a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp),
  );
}

function HeaderNotifications({ data }: { data: Dashboard }) {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<NotifCategory>("all");
  const [page, setPage] = useState(1);
  const [seenIds, setSeenIds] = useState<string[]>([]);
  const containerRef = useRef<HTMLDivElement>(null);

  const allItems = useMemo(() => buildNotifications(data), [data]);
  const unreadCount = useMemo(
    () => allItems.filter((item) => !seenIds.includes(item.id)).length,
    [allItems, seenIds],
  );

  const filtered = useMemo(
    () =>
      category === "all"
        ? allItems
        : allItems.filter((item) => item.category === category),
    [allItems, category],
  );

  const totalPages = Math.max(1, Math.ceil(filtered.length / NOTIF_PAGE_SIZE));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const pageItems = filtered.slice(
    (safePage - 1) * NOTIF_PAGE_SIZE,
    safePage * NOTIF_PAGE_SIZE,
  );

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const markAllSeen = () => {
    setSeenIds(allItems.map((item) => item.id));
  };

  return (
    <div className="notif-wrapper" ref={containerRef}>
      <button
        type="button"
        className={`notif-bell-button ${open ? "active" : ""}`}
        aria-label="Cổng thông báo hệ thống"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((prev) => !prev)}
      >
        <Icon name="bell" size={20} />
        {unreadCount > 0 && (
          <span className="notif-bell-badge" aria-label={`${unreadCount} thông báo mới`}>
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div
          className="notif-popover"
          role="dialog"
          aria-label="Cổng thông báo hệ thống"
        >
          <div className="notif-popover-header">
            <div>
              <strong>Cổng thông báo hệ thống</strong>
              <span className="small muted">
                Tổng hợp lịch sử tưới cây, kết quả ảnh AI và trạng thái vườn
              </span>
            </div>
            <div className="notif-header-actions">
              {unreadCount > 0 && (
                <button
                  type="button"
                  className="notif-mark-read"
                  onClick={markAllSeen}
                >
                  Đánh dấu đã đọc
                </button>
              )}
              <button
                type="button"
                className="notif-close-btn"
                aria-label="Đóng cổng thông báo"
                onClick={() => setOpen(false)}
              >
                ×
              </button>
            </div>
          </div>

          <div className="notif-tabs" role="tablist" aria-label="Lọc loại thông báo">
            {(
              [
                ["all", `Tất cả (${allItems.length})`],
                [
                  "watering",
                  `Tưới cây (${
                    allItems.filter((i) => i.category === "watering").length
                  })`,
                ],
                [
                  "image",
                  `Ảnh chụp cây (${
                    allItems.filter((i) => i.category === "image").length
                  })`,
                ],
                [
                  "system",
                  `Hệ thống (${
                    allItems.filter((i) => i.category === "system").length
                  })`,
                ],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={category === key}
                className={category === key ? "active" : ""}
                onClick={() => {
                  setCategory(key);
                  setPage(1);
                }}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="notif-list">
            {pageItems.length === 0 ? (
              <p className="notif-empty muted small">
                Chưa có thông báo nào trong mục này.
              </p>
            ) : (
              pageItems.map((item) => {
                const isUnread = !seenIds.includes(item.id);
                return (
                  <Link
                    key={item.id}
                    href={item.href}
                    className={`notif-item tone-${item.tone} ${
                      isUnread ? "unread" : ""
                    }`}
                    onClick={() => {
                      if (isUnread) {
                        setSeenIds((prev) => [...prev, item.id]);
                      }
                      setOpen(false);
                    }}
                  >
                    <span className="notif-item-icon">
                      <Icon name={item.icon} size={18} />
                    </span>
                    <div className="notif-item-content">
                      <div className="notif-item-top">
                        <span className="notif-item-tag">{item.tag}</span>
                        <span className="notif-item-time">
                          {formatDateTime(item.timestamp)}
                        </span>
                      </div>
                      <strong>{item.title}</strong>
                      <p>{item.detail}</p>
                    </div>
                  </Link>
                );
              })
            )}
          </div>

          <div className="notif-pagination">
            <span className="small muted">
              Trang {safePage}/{totalPages} ({filtered.length} mục)
            </span>
            <div className="notif-page-controls">
              <button
                type="button"
                disabled={safePage <= 1}
                aria-label="Trang thông báo trước"
                onClick={() => setPage(safePage - 1)}
              >
                ← Trước
              </button>
              {Array.from({ length: totalPages }, (_, idx) => idx + 1)
                .filter(
                  (p) =>
                    p === 1 ||
                    p === totalPages ||
                    Math.abs(p - safePage) <= 1,
                )
                .map((p) => (
                  <button
                    key={p}
                    type="button"
                    aria-current={p === safePage ? "page" : undefined}
                    className={p === safePage ? "active" : ""}
                    onClick={() => setPage(p)}
                  >
                    {p}
                  </button>
                ))}
              <button
                type="button"
                disabled={safePage >= totalPages}
                aria-label="Trang thông báo tiếp theo"
                onClick={() => setPage(safePage + 1)}
              >
                Sau →
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

type NavigationItem = {
  id: string;
  href: string;
  icon: string;
  label: string;
  title: string;
  description: string;
  feature?: keyof NonNullable<Dashboard["features"]>;
};

const navigation: NavigationItem[] = [
  {
    id: "overview",
    href: "/",
    icon: "grid",
    label: "Tổng quan",
    title: "Khu vườn hôm nay",
    description: "Những thông tin cần biết để chăm cây hôm nay.",
  },
  {
    id: "schedule",
    href: "/schedule",
    icon: "clock",
    label: "Lịch tưới",
    title: "Lịch tưới",
    description: "Thiết lập nhịp tưới và điều kiện thời tiết cho khu vườn.",
  },
  {
    id: "control",
    href: "/control",
    icon: "drop",
    label: "Điều khiển tưới",
    title: "Điều khiển tưới",
    description: "Gửi lệnh thủ công và theo dõi phản hồi từ thiết bị.",
  },
  {
    id: "ai",
    href: "/ai",
    icon: "image",
    label: "Phân tích AI",
    title: "Phân tích AI",
    description: "Theo dõi lá, độ chín của quả và dự báo mưa.",
  },
  {
    id: "chat",
    feature: "chat",
    href: "/chat",
    icon: "chat",
    label: "Trợ lý cây trồng",
    title: "Trợ lý cây trồng",
    description: "Hỏi về số đo, lịch tưới, dự báo mưa và kết quả phân tích.",
  },
  {
    id: "alerts",
    feature: "monitoring",
    href: "/alerts",
    icon: "bell",
    label: "Cảnh báo",
    title: "Cảnh báo",
    description: "Xem sự cố đang diễn ra và lịch sử phục hồi.",
  },
  {
    id: "history",
    href: "/history",
    icon: "clock",
    label: "Lịch sử tưới",
    title: "Lịch sử tưới",
    description: "Đối chiếu lệnh, thời điểm và phản hồi của thiết bị.",
  },
  {
    id: "simulation",
    href: "/simulation",
    icon: "bolt",
    label: "Mô phỏng",
    title: "Mô phỏng",
    description: "Thử các tình huống môi trường trên thiết bị mô phỏng.",
  },
];

function Sidebar({
  items,
  close,
  logout,
}: {
  items: NavigationItem[];
  close?: () => void;
  logout: () => void;
}) {
  const pathname = usePathname();
  return (
    <>
      <Link className="brand" href="/" onClick={close}>
        <Icon name="leaf" size={30} />
        plantcare<span>AIoT</span>
      </Link>
      <div className="garden-switch">
        <span className="garden-avatar">
          <Icon name="leaf" />
        </span>
        <div>
          <strong>Vườn cà chua</strong>
          <span>Không gian chăm sóc</span>
        </div>
      </div>
      <nav aria-label="Điều hướng chính">
        {items.map((item) => (
          <Link
            key={item.id}
            href={item.href}
            onClick={close}
            className={pathname === item.href ? "active" : ""}
            aria-current={pathname === item.href ? "page" : undefined}
          >
            <Icon name={item.icon} />
            <span>{item.label}</span>
          </Link>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <span className="badge">Thiết bị mô phỏng</span>
        <p>
          Dữ liệu từ simulator.
          <br />
          Không gian thử nghiệm AIoT.
        </p>
        <button
          className="text-button logout-button"
          onClick={() => {
            close?.();
            logout();
          }}
        >
          Đăng xuất <span aria-hidden="true">↗</span>
        </button>
      </div>
    </>
  );
}

export function AppShell({
  children,
  data,
  connected,
  apiOnline,
  logout,
}: {
  children: ReactNode;
  data: Dashboard;
  connected: boolean;
  apiOnline: boolean;
  logout: () => void;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const requested = navigation.find((item) => item.href === pathname);
  const current = requested ?? navigation[0];
  const items = navigation.filter(
    ({ feature }) => !feature || data.features?.[feature],
  );
  const featureDisabled =
    !!requested?.feature && !data.features?.[requested.feature];
  const drawer = useRef<HTMLDialogElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (featureDisabled) router.replace("/");
  }, [featureDisabled, router]);
  if (featureDisabled) return null;
  const close = () => {
    drawer.current?.close();
    setOpen(false);
    menuButton.current?.focus();
  };
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Đến nội dung chính
      </a>
      <aside className="sidebar">
        <Sidebar items={items} logout={logout} />
      </aside>
      <dialog
        ref={drawer}
        className="mobile-drawer"
        aria-label="Menu điều hướng"
        onClose={() => setOpen(false)}
        onClick={(e) => {
          if (e.target === e.currentTarget) close();
        }}
      >
        <button className="drawer-close button secondary" onClick={close}>
          Đóng menu ×
        </button>
        <Sidebar items={items} close={close} logout={logout} />
      </dialog>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              ref={menuButton}
              className="menu-button"
              aria-label="Mở menu"
              aria-expanded={open}
              onClick={() => {
                drawer.current?.showModal();
                setOpen(true);
              }}
            >
              ☰
            </button>
            <span>Vườn cà chua</span>
            <span className="slash">/</span>
            <strong>{current.label}</strong>
          </div>
          <div className="topbar-actions">
            <HeaderNotifications data={data} />
            <span
              className={`connection ${connected && apiOnline ? "" : "offline"}`}
            >
              <i />
              {connected && apiOnline
                ? "Đang cập nhật trực tiếp"
                : apiOnline
                  ? "REST dự phòng"
                  : "Mất kết nối backend"}
            </span>
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>
          <div className="page-heading">
            <div>
              <h1>{current.title}</h1>
              <p className="muted">{current.description}</p>
            </div>
            <div className="updated-at">
              <span>Lần đo gần nhất</span>
              <strong>
                {data.latest
                  ? time(data.latest.measured_at)
                  : "Chưa có dữ liệu"}
              </strong>
              <span>Giờ Việt Nam</span>
            </div>
          </div>
          <div className="demo-banner">
            <Icon name="leaf" size={18} />
            <span>
              Thiết bị đang mô phỏng; AI chạy model đã huấn luyện. Kết quả thiếu
              model sẽ báo không khả dụng.
            </span>
          </div>
          {children}
          <footer>
            <span>
              plantcare <span className="muted">/ AIoT</span>
            </span>
            <span>Asia/Ho_Chi_Minh · Môi trường mô phỏng</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
