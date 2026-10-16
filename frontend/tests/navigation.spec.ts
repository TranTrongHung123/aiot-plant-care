import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { nextSchedule } from "../src/lib/overview";
import type { Policy } from "../src/lib/api";

const token = readFileSync(path.resolve("../.env"), "utf8")
  .match(/^APP_TOKEN=(.+)$/m)?.[1]
  .trim();
if (!token) throw new Error("APP_TOKEN is required");

test("sitemap, responsive, drawer keyboard and read-only overview", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByLabel("Khóa truy cập").fill(token);
  await page.getByRole("button", { name: "Mở dashboard" }).click();
  await expect(
    page.getByRole("heading", { name: "Khu vườn hôm nay" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Tưới ngay", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Kiểu lịch lặp")).toHaveCount(0);
  await expect(page.locator(".scenarios")).toHaveCount(0);
  await expect(page.locator(".history-card")).toHaveCount(0);
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await page.screenshot({
      path: `../.runtime/overview-${width}.png`,
      fullPage: true,
    });
  }
  const menu = page.getByRole("button", { name: "Mở menu" });
  await menu.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(menu).toBeFocused();
  await menu.click();
  await page.getByRole("link", { name: "Lịch tưới", exact: true }).click();
  await expect(page).toHaveURL(/\/schedule$/);
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(page.getByLabel("Kiểu lịch lặp")).toBeVisible();
  await page.goBack();
  await expect(
    page.getByRole("heading", { name: "Khu vườn hôm nay" }),
  ).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1000 });
  for (const [label, route] of [
    ["Lịch tưới", "schedule"],
    ["Điều khiển tưới", "control"],
    ["Phân tích AI", "ai"],
    ["Lịch sử tưới", "history"],
    ["Mô phỏng", "simulation"],
  ]) {
    await page.getByRole("link", { name: label, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/${route}$`));
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(label);
    await expect(page.locator(".sidebar nav [aria-current=page]")).toHaveText(
      label,
    );
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBeTruthy();
      await page.screenshot({
        path: `../.runtime/${route}-${width}.png`,
        fullPage: true,
      });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  await page.reload();
  await page.getByLabel("Khóa truy cập").fill(token);
  await page.getByRole("button", { name: "Mở dashboard" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Mô phỏng");
  expect(errors).toEqual([]);
});

test("next schedule uses server time, Vietnam date and interval anchor", () => {
  const policy = {
    enabled: true,
    schedule_kind: "daily",
    daily_times: ["06:00", "18:00"],
    schedule_started_at: "2026-10-15T00:00:00Z",
    interval_minutes: 30,
  } as Policy;
  expect(nextSchedule(policy, "2026-10-16T12:00:00Z")).toBe(
    "2026-10-16T23:00:00.000Z",
  );
  expect(
    nextSchedule({ ...policy, enabled: false }, "2026-10-16T12:00:00Z"),
  ).toBeNull();
  expect(
    nextSchedule(
      { ...policy, schedule_kind: "interval" },
      "2026-10-16T12:04:00Z",
    ),
  ).toBe("2026-10-16T12:30:00.000Z");
  expect(
    nextSchedule({ ...policy, daily_times: [] }, "2026-10-16T12:00:00Z"),
  ).toBeNull();
});

test("history pagination limits rows and navigates real command history", async ({
  page,
}) => {
  await page.goto("/history");
  await page.getByLabel("Khóa truy cập").fill(token);
  await page.getByRole("button", { name: "Mở dashboard" }).click();
  const rows = page.locator("tbody tr");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeLessThanOrEqual(6);
  const next = page.getByRole("button", { name: "Trang sau", exact: true });
  if (await next.isEnabled()) {
    await next.click();
    await expect(
      page.getByRole("button", { name: "Trang 2", exact: true }),
    ).toHaveAttribute("aria-current", "page");
    await expect(page.locator(".pagination-bar [role=status]")).toContainText(
      "7–",
    );
    expect(await rows.count()).toBeLessThanOrEqual(6);
    await page
      .getByRole("button", { name: "Trang trước", exact: true })
      .click();
  }
  await expect(
    page.getByRole("button", { name: "Trang 1", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expect(
    page.getByRole("button", { name: "Trang trước", exact: true }),
  ).toBeDisabled();
  await rows.first().getByText("Xem chi tiết", { exact: true }).click();
  await expect(rows.first().locator("details")).toHaveAttribute("open", "");
});

test("backend connection loss shows cached data and recovery", async ({
  page,
  context,
}) => {
  await page.goto("/");
  await page.getByLabel("Khóa truy cập").fill(token);
  await page.getByRole("button", { name: "Mở dashboard" }).click();
  await expect(page.getByText("Đang cập nhật trực tiếp")).toBeVisible();
  try {
    await context.setOffline(true);
    await expect(page.locator(".message.error")).toContainText(
      "Backend chưa phản hồi",
      { timeout: 30000 },
    );
    await expect(page.locator(".metric.stale")).toHaveCount(4);
    await expect(page.locator(".plant-summary h2")).toHaveText(
      "Cần kiểm tra dữ liệu",
    );
    await page.screenshot({
      path: "../.runtime/overview-offline.png",
      fullPage: true,
    });
  } finally {
    await context.setOffline(false);
  }
  await expect(page.getByText("Đang cập nhật trực tiếp")).toBeVisible({
    timeout: 30000,
  });
  await expect(page.locator(".message.error")).toHaveCount(0);
});
