"use client";

import { useEffect, useState } from "react";

export function ActionFeedback({
  message,
  tone,
  dismiss,
}: {
  message: string;
  tone: "success" | "error";
  dismiss: () => void;
}) {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return;
    const timer = setTimeout(dismiss, tone === "success" ? 4000 : 8000);
    return () => clearTimeout(timer);
  }, [message, tone, paused, dismiss]);

  return (
    <div
      className={`message ${tone} action-feedback`}
      role={tone === "error" ? "alert" : "status"}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget))
          setPaused(false);
      }}
    >
      {message}
      <button
        onClick={dismiss}
        aria-label={tone === "error" ? "Đóng lỗi" : "Đóng thông báo"}
      >
        ×
      </button>
    </div>
  );
}
