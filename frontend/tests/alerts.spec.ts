import { expect, test } from "@playwright/test";

const at = "2026-11-22T10:20:00+00:00";
const dashboard = {
  latest: null,
  jobs: [],
  history: [],
  stale: true,
  mqtt_connected: true,
  device: { name: "Vườn demo", pump: "off", online: true },
  server_time: at,
  policy: {
    enabled: false,
    threshold: 30,
    schedule_kind: "daily",
    daily_times: [],
  },
};
const cors = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, OPTIONS",
  "access-control-allow-headers": "authorization, content-type",
};

test("dashboard shows new alerts and history keeps seen, recovered, and cached states", async ({
  page,
}) => {
  const item = {
    id: "6be6bae0-b76d-4e85-8e63-d98a2e26b870",
    kind: "device_offline",
    trigger_type: "condition",
    severity: "warning",
    source_kind: "device",
    source_ref: "sim-esp32-01",
    data_source: "simulation",
    observed_at: at,
    created_at: at,
    acknowledged_at: null as string | null,
    resolved_at: null as string | null,
    title: "Mất kết nối thiết bị",
    description: "Thiết bị không còn gửi trạng thái trong khoảng theo dõi.",
    evidence: {},
  };
  let exists = false;
  let unavailable = false;
  await page.clock.install();
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS")
      return route.fulfill({ headers: cors, status: 204 });
    const response = (json: unknown, status = 200) =>
      route.fulfill({ headers: cors, status, json });
    if (url.pathname.endsWith("/dashboard")) {
      return response({
        ...dashboard,
        features: { chat: false, monitoring: true },
      });
    }
    if (url.pathname.endsWith("/alerts")) {
      if (unavailable) return response({ detail: "Offline" }, 503);
      const filter = url.searchParams.get("state");
      const visible =
        exists &&
        (filter === "all" ||
          (filter === "attention" && !item.resolved_at) ||
          (filter === "unread" && !item.acknowledged_at));
      return response({
        items: visible ? [item] : [],
        unread_count: Number(exists && !item.acknowledged_at),
        next_cursor: null,
      });
    }
    if (
      url.pathname.endsWith(`/${item.id}/acknowledge`) &&
      request.method() === "POST"
    ) {
      item.acknowledged_at ??= at;
      return response(item);
    }
    throw new Error(`Unexpected request: ${url.pathname}`);
  });

  await page.goto("/");
  await page.getByLabel("Khóa truy cập").fill("browser-monitoring-test-token");
  await page.getByRole("button", { name: "Mở dashboard" }).click();
  await expect(
    page.getByText("Chưa có cảnh báo trong danh sách này."),
  ).toBeVisible();
  exists = true;
  await page.clock.fastForward(10000);
  await expect(page.getByRole("heading", { name: item.title })).toBeVisible();
  await expect(page.getByText("1 chưa xem")).toBeVisible();

  await page.getByRole("link", { name: "Xem lịch sử cảnh báo" }).click();
  await expect(
    page.getByRole("heading", { name: "Lịch sử cảnh báo" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Đánh dấu đã xem" }).click();
  await expect(page.getByText("Đã xem", { exact: true })).toBeVisible();
  await expect(page.getByText("Đang diễn ra", { exact: true })).toBeVisible();
  await expect(page.getByText("0 chưa xem")).toBeVisible();

  item.resolved_at = "2026-11-22T10:25:00+00:00";
  await page.getByLabel("Trạng thái").selectOption("all");
  await expect(page.getByText("Đã phục hồi", { exact: true })).toBeVisible();
  unavailable = true;
  await page.clock.fastForward(10000);
  await expect(
    page.getByText("Đang hiển thị danh sách cảnh báo nhận được lần cuối."),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: item.title })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 900 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("disabled assistant and alerts are hidden and direct links return to overview", async ({
  page,
}) => {
  const optionalRequests: string[] = [];
  page.on("request", (request) => {
    if (
      /\/api\/v1\/(chat|alerts)(\/|$)/.test(new URL(request.url()).pathname)
    ) {
      optionalRequests.push(request.url());
    }
  });
  await page.route("**/api/v1/dashboard", (route) =>
    route.request().method() === "OPTIONS"
      ? route.fulfill({ headers: cors, status: 204 })
      : route.fulfill({
          headers: cors,
          json: { ...dashboard, features: { chat: false, monitoring: false } },
        }),
  );
  for (const path of ["/chat", "/alerts"]) {
    await page.goto(path);
    await page.getByLabel("Khóa truy cập").fill("browser-features-test-token");
    await page.getByRole("button", { name: "Mở dashboard" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(
      page.getByRole("heading", { name: "Khu vườn hôm nay" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Phân tích AI", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Lịch tưới", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Trợ lý cây trồng" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("link", { name: "Cảnh báo", exact: true }),
    ).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Cần chú ý" })).toHaveCount(
      0,
    );
  }
  expect(optionalRequests).toEqual([]);
});
