import { expect, test } from "@playwright/test";

test("chat persists a conversation and shows provider, evidence, and mobile layout", async ({
  page,
}) => {
  const at = "2026-11-22T10:20:00+00:00";
  const documentUrl =
    "https://extension.umn.edu/garden-and-home/yard-and-garden/gardening-in-minnesota/yard-and-garden-problems/tomato-leaf-spot-diseases";
  const assistant = {
    id: "a1",
    role: "assistant",
    content: "Độ ẩm đất đo được là 18%.",
    details: {
      llm: { actual_provider: "local", model: "fixture", fallback_used: true },
      agents_used: ["iot", "plant_health", "knowledge"],
      sources: [
        {
          kind: "sensor",
          ref: "r1",
          timestamp: at,
          data_source: "simulation",
          stale: false,
        },
        {
          kind: "image",
          ref: "image1",
          timestamp: at,
          model_version: "fixture-image",
        },
        {
          kind: "knowledge",
          ref: "plant-knowledge-v1:septoria",
          citation: {
            title: "Tomato leaf spot diseases",
            publisher: "University of Minnesota Extension",
            section: "What to look for?",
            version: "plant-knowledge-v1",
            revision: "2018",
            url: documentUrl,
          },
        },
        {
          kind: "knowledge",
          ref: "plant-knowledge-v1:sensor-reading",
          citation: {
            title: "Hợp đồng dữ liệu AIoT Plant Care",
            publisher: "AIoT Plant Care",
            section: "MQTT → Telemetry",
            version: "plant-knowledge-v1",
            revision: "Phase 2",
            url: null,
          },
        },
      ],
    },
  };
  const user = { id: "u1", role: "user", content: "Độ ẩm đất?", details: {} };
  const thread = { id: "c1", default_mode: "auto", title: user.content };
  let messages: Array<typeof user | typeof assistant> = [];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.split("/api/v1")[1];
    const headers = {
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET, POST, OPTIONS",
      "access-control-allow-headers": "authorization, content-type",
    };
    if (request.method() === "OPTIONS")
      return route.fulfill({ status: 204, headers });
    let body: unknown;
    if (path === "/dashboard") {
      body = {
        features: { chat: true, monitoring: false },
        latest: null,
        jobs: [],
        device: { pump: "off" },
        stale: true,
      };
    } else if (path === "/chat/providers") {
      body = { default_mode: "cloud", auto_preference: "cloud", providers: [] };
    } else if (path === "/chat/health") {
      body = { providers: { cloud: true, local: true } };
    } else if (path === "/chat/conversations") {
      body =
        request.method() === "POST"
          ? thread
          : {
              items: messages.length ? [thread] : [],
              next_offset: null,
            };
    } else if (path === "/chat/conversations/c1/messages") {
      if (request.method() === "POST") {
        expect(request.postDataJSON()).toMatchObject({
          message: user.content,
          mode: "auto",
        });
        messages = [user, assistant];
        body = {
          conversation: thread,
          user_message: user,
          assistant_message: assistant,
        };
      } else {
        body = {
          conversation: thread,
          items: messages,
          next_before_turn: null,
        };
      }
    } else {
      throw new Error(`Unexpected request: ${path}`);
    }
    return route.fulfill({ headers, json: body });
  });

  async function login() {
    await page.getByLabel("Khóa truy cập").fill("browser-fixture-token");
    await page.getByRole("button", { name: "Mở dashboard" }).click();
    await expect(
      page.getByRole("heading", { name: "Trợ lý cây trồng" }),
    ).toBeVisible();
  }

  await page.goto("/chat");
  await login();
  await page.getByLabel("Mô hình").selectOption("auto");
  await page.getByLabel("Câu hỏi của bạn").fill(user.content);
  await page.getByRole("button", { name: "Gửi câu hỏi" }).click();
  await expect(page.getByText(assistant.content)).toBeVisible();
  await expect(
    page.getByText(/Local · fixture · Đã chuyển nhà cung cấp/),
  ).toBeVisible();
  await page.getByText("Nguồn dữ liệu", { exact: true }).click();
  await expect(page.getByText(/Số đo · .* · nguồn simulation/)).toBeVisible();
  await expect(
    page.getByText(/Kết quả ảnh · .* · fixture-image/),
  ).toBeVisible();
  const citation = page.getByRole("link", {
    name: "Tomato leaf spot diseases",
  });
  await expect(citation).toHaveAttribute("href", documentUrl);
  await expect(citation.locator("..")).toContainText(
    "What to look for? · 2018 (plant-knowledge-v1)",
  );
  await expect(
    page.getByText(/Hợp đồng dữ liệu AIoT Plant Care.*MQTT → Telemetry/),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Hợp đồng dữ liệu AIoT Plant Care" }),
  ).toHaveCount(0);

  await page.reload();
  await login();
  await page.getByRole("button", { name: user.content, exact: true }).click();
  await expect(page.getByText(assistant.content)).toBeVisible();
  await expect(page.getByLabel("Mô hình")).toHaveValue("auto");
  await page.getByText("Nguồn dữ liệu", { exact: true }).click();
  await expect(citation).toHaveAttribute("href", documentUrl);
  await page.setViewportSize({ width: 390, height: 900 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
