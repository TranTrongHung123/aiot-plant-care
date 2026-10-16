import { expect, test } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const token = readFileSync(path.resolve("../.env"), "utf8")
  .match(/^APP_TOKEN=(.+)$/m)?.[1]
  .trim();
if (!token) throw new Error("APP_TOKEN is required for local E2E tests");

test("dashboard → scenario → trained AI upload → persisted history → mobile", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const login = async () => {
    await page.getByLabel("Khóa truy cập").fill(token);
    await page.getByRole("button", { name: "Mở dashboard" }).click();
    await expect(page.locator("#main-content")).toBeVisible();
  };
  await page.goto("/");
  await login();
  await expect(page.locator(".demo-banner")).toContainText(
    "AI chạy model đã huấn luyện",
  );
  await expect(page.getByText("Đang cập nhật trực tiếp")).toBeVisible();
  await page
    .getByRole("link", { name: "Điều khiển tưới", exact: true })
    .click();
  await page.getByRole("button", { name: "Dừng bơm", exact: true }).click();
  await page.getByRole("link", { name: "Mô phỏng", exact: true }).click();
  await page.getByRole("button", { name: "Đất khô", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Đất khô", exact: true }),
  ).toHaveClass("selected");
  await page.getByRole("link", { name: "Tổng quan", exact: true }).click();
  await expect
    .poll(
      async () =>
        Number(
          (
            await page
              .locator(".metric")
              .nth(0)
              .locator(".metric-value")
              .innerText()
          ).replace("%", ""),
        ),
      { timeout: 15000 },
    )
    .toBeLessThan(30);
  await page.getByRole("link", { name: "Mô phỏng", exact: true }).click();
  await page.getByRole("button", { name: "Bình thường", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Bình thường", exact: true }),
  ).toHaveClass("selected");
  await page.getByRole("link", { name: "Lịch tưới", exact: true }).click();
  const card = page.locator(".weather-card");
  const originalMode = await page.request
    .get("http://localhost:8000/api/v1/dashboard", {
      headers: { Authorization: `Bearer ${token}` },
    })
    .then((r) => r.json())
    .then((d) => d.weather_control);
  try {
    await card.getByRole("button", { name: "DEMO", exact: true }).click();
    await card.getByRole("button", { name: "Có mưa", exact: true }).click();
    await expect(card.locator(".weather-decision")).toContainText(
      "DEMO có mưa",
    );
    await expect(card).toContainText("không phải dự đoán XGBoost");
    await card.getByRole("button", { name: "Không mưa", exact: true }).click();
    await expect(card.locator(".weather-decision")).toContainText(
      "DEMO không mưa",
    );
    await page.reload();
    await login();
    await expect(
      card.getByRole("button", { name: "DEMO", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(
      card.getByRole("button", { name: "Không mưa", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await page.getByLabel("Kiểu lịch lặp").selectOption("interval");
    await expect(
      page.getByRole("spinbutton", { name: "Chu kỳ (phút)" }),
    ).toBeVisible();
    await page.getByLabel("Kiểu lịch lặp").selectOption("daily");
    await expect(
      page.getByRole("textbox", { name: "Giờ tưới hằng ngày" }),
    ).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBeTruthy();
    await page.screenshot({
      path: "../.runtime/weather-demo-mobile.png",
      fullPage: false,
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await card.getByRole("button", { name: "LIVE", exact: true }).click();
    await expect(card).toContainText("XGBOOST / OPENWEATHER");
    await expect(
      card.getByRole("button", { name: "Có mưa", exact: true }),
    ).toHaveCount(0);
  } finally {
    await page.request.put("http://localhost:8000/api/v1/weather/mode", {
      headers: { Authorization: `Bearer ${token}` },
      data: originalMode,
    });
  }
  await page.screenshot({
    path: "../.runtime/dashboard-desktop.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "Phân tích AI", exact: true }).click();
  await page
    .getByLabel("Chọn ảnh cây")
    .setInputFiles("tests/fixtures/tomato-demo.png");
  await page.getByRole("button", { name: "Tải ảnh & chạy AI" }).click();
  await expect(page.locator(".result-card .confidence")).toBeVisible({
    timeout: 90000,
  });
  await expect(page.getByAltText("Ảnh cây đã tải lên")).toBeVisible();
  await page.getByRole("button", { name: "Độ chín · YOLO" }).click();
  const fruitFixture = path.resolve(
    "../model-artifacts/fruit/full-20261015T040727_579719Z/inference-tomato.jpg",
  );
  await page
    .getByLabel("Chọn ảnh cây")
    .setInputFiles(
      existsSync(fruitFixture)
        ? fruitFixture
        : "tests/fixtures/tomato-demo.png",
    );
  await page.getByRole("button", { name: "Tải ảnh & chạy AI" }).click();
  await expect(page.locator(".result-card h2")).toContainText("quả", {
    timeout: 15000,
  });
  // A synthetic fixture may legitimately contain zero detectable fruit.
  await expect(page.locator(".result-card")).toContainText(
    "full-20261015T040727_579719Z",
  );
  if (existsSync(fruitFixture)) {
    await expect(page.locator(".detection").first()).toBeVisible();
  }
  await page.screenshot({ path: "../.runtime/ai-desktop.png", fullPage: true });
  await page.reload();
  await login();
  await page.getByRole("link", { name: "Phân tích AI", exact: true }).click();
  await expect(page.locator(".result-card h2")).toContainText("quả");
  await page.getByRole("link", { name: "Lịch sử tưới", exact: true }).click();
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Mở menu", exact: true }).click();
  await page.getByRole("link", { name: "Tổng quan", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Khu vườn hôm nay" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../.runtime/dashboard-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
