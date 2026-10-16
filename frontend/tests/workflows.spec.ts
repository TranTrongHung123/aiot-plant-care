import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

const token = readFileSync(path.resolve("../.env"), "utf8")
  .match(/^APP_TOKEN=(.+)$/m)?.[1]
  .trim();
if (!token) throw new Error("APP_TOKEN is required");
const headers = { Authorization: `Bearer ${token}` };

test("action feedback dismisses automatically while realtime snapshots continue", async ({
  page,
}) => {
  await page.goto("/schedule");
  await page.getByLabel("Khóa truy cập").fill(token);
  await page.getByRole("button", { name: "Mở dashboard" }).click();
  await expect(page.getByText("Đang cập nhật trực tiếp")).toBeVisible();
  const save = page.getByRole("button", { name: "Lưu quy tắc", exact: true });
  const notice = page.locator(".action-feedback");
  await save.click();
  await expect(notice).toContainText("Đã lưu quy tắc tưới.");
  await page.mouse.move(0, 0);
  await expect(notice).toHaveCount(0, { timeout: 6500 });
  await save.click();
  await expect(notice).toBeVisible();
  await page
    .getByRole("button", { name: "Đóng thông báo", exact: true })
    .click();
  await expect(notice).toHaveCount(0);
  await page.route("**/api/v1/irrigation/policy", (route) =>
    route.abort("failed"),
  );
  await save.click();
  await expect(notice).toHaveAttribute("role", "alert");
  await page.mouse.move(0, 0);
  await expect(notice).toHaveCount(0, { timeout: 10500 });
  await expect(page.getByText("Đang cập nhật trực tiếp")).toBeVisible();
});

test("saved policy stays visible and weather switches do not shift layout", async ({
  page,
}) => {
  const initial = await page.request
    .get("http://localhost:8000/api/v1/dashboard", { headers })
    .then((response) => response.json());
  await page.goto("/schedule");
  await page.getByLabel("Khóa truy cập").fill(token);
  await page.getByRole("button", { name: "Mở dashboard" }).click();
  const summary = page.locator(".saved-policy");
  await expect(summary).toContainText(`${initial.policy.threshold}%`);
  await expect(summary).toContainText(
    `${initial.policy.duration_seconds} giây`,
  );
  const form = page.locator(".schedule-form-card");
  const formTop = () =>
    form.evaluate(
      (element) => element.getBoundingClientRect().top + window.scrollY,
    );
  const before = await formTop();
  await page.getByRole("button", { name: "Lưu quy tắc", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Đã lưu quy tắc tưới." }),
  ).toBeVisible();
  expect(Math.abs((await formTop()) - before)).toBeLessThan(2);
  const panel = page.locator(".weather-panel");
  const draftThreshold = Math.min(60, initial.policy.threshold + 1);
  await page
    .getByRole("spinbutton", { name: "Ngưỡng đất (%)", exact: true })
    .fill(String(draftThreshold));
  try {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      const positions: { y: number; height: number }[] = [];
      for (const mode of ["DEMO", "LIVE", "DEMO", "LIVE"]) {
        await panel.getByRole("button", { name: mode, exact: true }).click();
        await expect(
          panel.getByRole("button", { name: mode, exact: true }),
        ).toHaveAttribute("aria-pressed", "true");
        await expect(
          panel.getByRole("button", { name: mode, exact: true }),
        ).toBeEnabled();
        positions.push(
          await panel.evaluate((element) => {
            const box = element.getBoundingClientRect();
            return { y: box.y + window.scrollY, height: box.height };
          }),
        );
        await expect(
          panel.locator('.weather-mode-pane[data-active="false"]'),
        ).toHaveAttribute("inert", "");
      }
      expect(
        Math.max(...positions.map((p) => p.height)) -
          Math.min(...positions.map((p) => p.height)),
      ).toBeLessThan(3);
      await expect(
        page.getByRole("spinbutton", { name: "Ngưỡng đất (%)", exact: true }),
      ).toHaveValue(String(draftThreshold));
      expect(
        Math.max(...positions.map((p) => p.y)) -
          Math.min(...positions.map((p) => p.y)),
      ).toBeLessThan(3);
      await page
        .getByRole("spinbutton", { name: "Ngưỡng đất (%)", exact: true })
        .fill(String(initial.policy.threshold));
      const dismiss = page.getByRole("button", {
        name: "Đóng thông báo",
        exact: true,
      });
      if (await dismiss.isVisible()) await dismiss.click();
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: `../.runtime/saved-policy-${width}.png`,
        fullPage: true,
        animations: "disabled",
      });
      await page
        .getByRole("spinbutton", { name: "Ngưỡng đất (%)", exact: true })
        .fill(String(draftThreshold));
    }
  } finally {
    await page.request.put("http://localhost:8000/api/v1/weather/mode", {
      headers,
      data: initial.weather_control,
    });
  }
});

test("late REST snapshot cannot revert a newer weather mode from WebSocket", async ({
  page,
}) => {
  const initial = await page.request
    .get("http://localhost:8000/api/v1/dashboard", { headers })
    .then((response) => response.json());
  await page.goto("/schedule");
  await page.getByLabel("Khóa truy cập").fill(token);
  await page.getByRole("button", { name: "Mở dashboard" }).click();
  await expect(page.getByText("Đang cập nhật trực tiếp")).toBeVisible();
  let captured = false;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const dashboardUrl = "http://localhost:8000/api/v1/dashboard";
  await page.route(dashboardUrl, async (route) => {
    if (captured) return route.continue();
    const response = await route.fetch();
    captured = true;
    await gate;
    await route.fulfill({ response });
  });
  const nextMode = initial.weather_control.mode === "live" ? "demo" : "live";
  try {
    await expect.poll(() => captured, { timeout: 15000 }).toBe(true);
    await page.request.put("http://localhost:8000/api/v1/weather/mode", {
      headers,
      data: { mode: nextMode, demo_rain: initial.weather_control.demo_rain },
    });
    const selected = page
      .locator(".weather-panel")
      .getByRole("button", { name: nextMode.toUpperCase(), exact: true });
    await expect(selected).toHaveAttribute("aria-pressed", "true");
    const lateResponse = page.waitForResponse(dashboardUrl);
    release();
    await (await lateResponse).finished();
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await expect(selected).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("Đang cập nhật trực tiếp")).toBeVisible();
  } finally {
    release();
    await page.unroute(dashboardUrl);
    await page.request.put("http://localhost:8000/api/v1/weather/mode", {
      headers,
      data: initial.weather_control,
    });
  }
});

test("AI timeline sorts by time, groups dates and filters forecast history", async ({
  page,
}) => {
  await page.goto("/ai");
  await page.getByLabel("Khóa truy cập").fill(token);
  await page.getByRole("button", { name: "Mở dashboard" }).click();
  await expect(page.locator(".timeline-entry").first()).toBeVisible();
  const dates = await page
    .locator(".timeline-entry")
    .evaluateAll((items) =>
      items.map((item) => Date.parse(item.getAttribute("data-created-at")!)),
    );
  expect(dates).toEqual([...dates].sort((a, b) => b - a));
  expect(dates.length).toBeLessThanOrEqual(6);
  await expect(page.locator(".timeline-day").first()).toHaveText(
    /\d{2}\/\d{2}\/\d{4}/,
  );
  await page.getByLabel("Loại phân tích").selectOption("weather");
  for (const heading of await page
    .locator(".timeline-content h3")
    .allTextContents())
    expect(heading).toBe("Dự báo mưa");
  if (await page.locator(".timeline-entry").count()) {
    await page
      .locator(".timeline-entry")
      .first()
      .getByText("Thông tin tác vụ", { exact: true })
      .click();
    await expect(
      page.locator(".timeline-entry").first().locator("details"),
    ).toHaveAttribute("open", "");
  }
  await page.getByLabel("Loại phân tích").selectOption("all");
  const openDetails = page.locator(".timeline-entry details[open] summary");
  if (await openDetails.count()) await openDetails.first().click();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: `../.runtime/ai-timeline-${width}.png`,
      fullPage: true,
      animations: "disabled",
    });
  }
});
