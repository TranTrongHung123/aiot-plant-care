"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useRef, useState } from "react";
import { Dashboard, time } from "@/lib/api";
import { Icon } from "./dashboard";

export const navigation = [
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
  close,
  logout,
}: {
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
        {navigation.map((item) => (
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
  const current =
    navigation.find((item) => item.href === pathname) ?? navigation[0];
  const drawer = useRef<HTMLDialogElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
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
        <Sidebar logout={logout} />
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
        <Sidebar close={close} logout={logout} />
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
