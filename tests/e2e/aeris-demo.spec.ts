import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const screenshots = resolve(projectRoot, "output/screenshots");
const composeProject = process.env.AERIS_COMPOSE_PROJECT ?? "aeris-control";
const apiUrl = process.env.AERIS_API_URL ?? "http://127.0.0.1:4100";

type ComposeContainer = {
  Service: string;
  State: string;
  Health?: string;
};

type Alarm = {
  id: string;
  deviceId: string;
  status: "ACTIVE" | "ACKNOWLEDGED" | "CLOSED";
  acknowledgedAt: string | null;
};

type Command = {
  id: string;
  deviceId: string;
  kind: "START" | "STOP" | "SETPOINT";
  value: number | null;
  status: "PENDING" | "ACKNOWLEDGED" | "REJECTED" | "TIMED_OUT";
  acknowledgedAt: string | null;
};

type EquipmentDetail = {
  state: {
    timestamp: string;
    temperature: number;
    pressure: number;
    power: number;
    connected: boolean;
  } | null;
  history: Array<{
    timestamp: string;
    temperature: number;
    pressure: number;
    power: number;
    connected: boolean;
  }>;
};

test.describe.configure({ mode: "serial" });

function composeContainers(): ComposeContainer[] {
  const output = execFileSync("docker", ["compose", "-p", composeProject, "-f", "compose.yaml", "ps", "--format", "json"], {
    cwd: projectRoot,
    encoding: "utf8",
  }).trim();
  if (!output) return [];
  if (output.startsWith("[")) return JSON.parse(output) as ComposeContainer[];
  return output.split("\n").map((line) => JSON.parse(line) as ComposeContainer);
}

function restartSimulator(): void {
  execFileSync("docker", ["compose", "-p", composeProject, "-f", "compose.yaml", "restart", "simulator"], {
    cwd: projectRoot,
    encoding: "utf8",
  });
}

function simulatorLogs(since: Date): string {
  return execFileSync(
    "docker",
    ["compose", "-p", composeProject, "-f", "compose.yaml", "logs", "--since", since.toISOString(), "simulator"],
    { cwd: projectRoot, encoding: "utf8" },
  );
}

async function equipmentDetail(
  request: APIRequestContext,
  start: Date,
  end: Date,
): Promise<EquipmentDetail> {
  let detail: EquipmentDetail | undefined;
  await expect.poll(async () => {
    const response = await request.get(`${apiUrl}/api/equipment/AHU-204`, {
      params: { start: start.toISOString(), end: end.toISOString() },
    });
    if (!response.ok()) return false;
    detail = await response.json() as EquipmentDetail;
    return true;
  }, { message: "AHU-204 history should be readable" }).toBe(true);
  return detail as EquipmentDetail;
}

async function assertNoHorizontalOverflow(page: Page) {
  await expect.poll(() => page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }))).toEqual(expect.objectContaining({ clientWidth: expect.any(Number) }));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
}

async function waitForActiveAlarm(page: Page, excludedIds = new Set<string>()): Promise<Alarm> {
  let active: Alarm | undefined;
  await expect.poll(async () => {
    const response = await page.request.get("/api/alarms");
    expect(response.ok()).toBe(true);
    const alarms = await response.json() as Alarm[];
    active = alarms.find((alarm) => (
      alarm.deviceId === "AHU-204"
      && alarm.status === "ACTIVE"
      && !excludedIds.has(alarm.id)
    ));
    return active?.id ?? null;
  }, { timeout: 30_000, message: "AHU-204 should enter a real high-pressure alarm cycle" }).not.toBeNull();
  return active as Alarm;
}

async function acknowledgeExactActiveAlarm(page: Page): Promise<Alarm> {
  const observedIds = new Set<string>();
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const active = await waitForActiveAlarm(page, observedIds);
    observedIds.add(active.id);
    await page.reload();
    const card = page.locator(`[data-alarm-id="${active.id}"]:visible`);
    const button = card.getByRole("button", { name: "Acknowledge AHU-204 alarm" });
    try {
      await expect(button).toBeVisible({ timeout: 3_000 });
    } catch {
      continue;
    }

    const responsePromise = page.waitForResponse((response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/alarms/${active.id}/ack`
    ));
    await button.click();
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    const acknowledged = await response.json() as Alarm;
    expect(acknowledged.id).toBe(active.id);

    // A natural recovery can close this exact alarm between the GET and POST;
    // the API intentionally returns that existing CLOSED state with HTTP 200.
    if (acknowledged.status === "CLOSED") continue;

    expect(acknowledged.status).toBe("ACKNOWLEDGED");
    expect(acknowledged.acknowledgedAt).not.toBeNull();
    expect(Number.isNaN(Date.parse(acknowledged.acknowledgedAt ?? ""))).toBe(false);
    return acknowledged;
  }
  throw new Error("AHU-204 alarm recovered before acknowledgement in every retry window");
}

test.beforeAll(async ({ request }) => {
  const expectedServices = ["backend", "clickhouse", "mosquitto", "redis", "simulator", "web"];
  await expect.poll(() => {
    const byService = new Map(composeContainers().map((container) => [container.Service, container]));
    return expectedServices.every((service) => {
      const container = byService.get(service);
      return container?.State === "running" && container.Health === "healthy";
    });
  }, { timeout: 120_000, message: "all runtime containers should be running and healthy" }).toBe(true);

  const health = await request.get(`${apiUrl}/api/health`);
  expect(health.ok()).toBe(true);
  expect(await health.json()).toMatchObject({
    status: "ok",
    dependencies: {
      database: "ok",
      redis: "ok",
      clickhouse: "ok",
      mqtt: "ok",
    },
    metrics: {
      telemetry: expect.any(Object),
      acknowledgements: expect.any(Object),
    },
  });
});

test("real Aeris workflow: alarm acknowledgement and acknowledged command", async ({ page }) => {
  mkdirSync(screenshots, { recursive: true });
  const consoleWarningsAndErrors: string[] = [];
  const failedRequests: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "warning" || message.type() === "error") {
      consoleWarningsAndErrors.push(`${message.type()}: ${message.text()}`);
    }
  });
  page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText ?? "failed"}`));

  const initialAlarm = await waitForActiveAlarm(page);

  await page.setViewportSize({ width: 1536, height: 1024 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Live operations" })).toBeVisible();
  await expect(page.locator(".desktop-topbar").getByText("System healthy", { exact: true })).toBeVisible();
  await expect(page.getByTestId("equipment-row")).toHaveCount(12);
  await expect(page.getByTestId("equipment-row").filter({ hasText: "AHU-204" })).toBeVisible();
  await expect(page.locator(".side-rail").getByText("High discharge pressure", { exact: false })).toBeVisible();
  await expect(page.locator(`[data-alarm-id="${initialAlarm.id}"]:visible`).getByRole("button", { name: "Acknowledge AHU-204 alarm" })).toBeVisible();
  await expect(page.locator(".telemetry-line")).toBeVisible();
  await expect(page.locator(".current-marker")).toBeVisible();
  await expect.poll(async () => {
    const end = new Date();
    const start = new Date(end.getTime() - 60 * 60 * 1_000);
    const response = await page.request.get(`/api/equipment/AHU-204?start=${start.toISOString()}&end=${end.toISOString()}`);
    expect(response.ok()).toBe(true);
    const detail = await response.json() as { history: Array<{ timestamp: string }> };
    if (detail.history.length < 2) return 0;
    return Date.parse(detail.history.at(-1)?.timestamp ?? "") - Date.parse(detail.history[0].timestamp);
  }, { timeout: 30_000, message: "AHU-204 chart should contain a meaningful recent time span" }).toBeGreaterThanOrEqual(45 * 60 * 1_000);
  await assertNoHorizontalOverflow(page);
  await page.screenshot({ path: resolve(screenshots, "aeris-overview-desktop.png") });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: "Live operations" })).toBeVisible();
  await expect(page.locator(`[data-alarm-id="${initialAlarm.id}"]:visible`).getByRole("button", { name: "Acknowledge AHU-204 alarm" })).toBeVisible();
  await assertNoHorizontalOverflow(page);
  await page.screenshot({ path: resolve(screenshots, "aeris-overview-mobile.png") });

  const acknowledgedAlarm = await acknowledgeExactActiveAlarm(page);
  expect(acknowledgedAlarm.deviceId).toBe("AHU-204");

  await page.setViewportSize({ width: 1536, height: 1024 });
  await page.getByRole("link", { name: "View AHU-204" }).click();
  await expect(page.getByRole("heading", { name: "AHU-204" })).toBeVisible();
  await expect(page.locator(".connection-label")).toHaveText(/Running/);

  await page.getByRole("combobox", { name: "Command" }).selectOption("SETPOINT");
  await page.getByRole("spinbutton", { name: "Value" }).fill("22.5");
  const commandResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/commands"
  ));
  await page.getByRole("button", { name: "Send command" }).click();
  const commandResponse = await commandResponsePromise;
  expect(commandResponse.status()).toBe(201);
  const createdCommand = await commandResponse.json() as Command;
  expect(createdCommand).toMatchObject({
    deviceId: "AHU-204",
    kind: "SETPOINT",
    value: 22.5,
    status: "PENDING",
    acknowledgedAt: null,
  });
  let acknowledgedCommand: Command | undefined;
  await expect.poll(async () => {
    const response = await page.request.get("/api/commands?deviceId=AHU-204");
    expect(response.ok()).toBe(true);
    const commands = await response.json() as Command[];
    acknowledgedCommand = commands.find((command) => command.id === createdCommand.id);
    return acknowledgedCommand ?? null;
  }, { timeout: 15_000, message: "the newly created SETPOINT 22.5 command should be acknowledged" }).toEqual(expect.objectContaining({
    id: createdCommand.id,
    deviceId: "AHU-204",
    kind: "SETPOINT",
    value: 22.5,
    status: "ACKNOWLEDGED",
    acknowledgedAt: expect.any(String),
  }));
  expect(Number.isNaN(Date.parse(acknowledgedCommand?.acknowledgedAt ?? ""))).toBe(false);
  const createdCommandRow = page.locator(`[data-command-id="${createdCommand.id}"]`);
  await expect(createdCommandRow).toContainText("SETPOINT 22.5");
  await expect(createdCommandRow.getByText("Acknowledged", { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(screenshots, "aeris-command-acknowledged.png"), fullPage: true });

  expect(consoleWarningsAndErrors).toEqual([]);
  expect(failedRequests).toEqual([]);
});

test("persisted simulator restart does not replay historical telemetry", async ({ page, request }) => {
  const activeBefore = await waitForActiveAlarm(page);
  const historyEnd = new Date(Date.now() - 5_000);
  const historyStart = new Date(historyEnd.getTime() - 60 * 60 * 1_000);
  const detailBefore = await equipmentDetail(request, historyStart, historyEnd);
  expect(detailBefore.state).not.toBeNull();
  expect(detailBefore.history.length).toBeGreaterThan(0);

  const restartStartedAt = new Date();
  restartSimulator();

  await expect.poll(() => {
    const simulator = composeContainers().find((container) => container.Service === "simulator");
    return simulator?.State === "running" && simulator.Health === "healthy";
  }, { timeout: 60_000, message: "restarted simulator should become healthy" }).toBe(true);

  const detailAfter = await equipmentDetail(request, historyStart, historyEnd);
  expect(detailAfter.history).toEqual(detailBefore.history);
  expect(simulatorLogs(restartStartedAt)).toContain("simulator_recent_history_skipped");
  expect(detailAfter.state).not.toBeNull();
  expect(Date.parse(detailAfter.state?.timestamp ?? "")).toBeGreaterThanOrEqual(
    Date.parse(detailBefore.state?.timestamp ?? ""),
  );
  expect(Date.parse(detailAfter.state?.timestamp ?? "")).toBeGreaterThanOrEqual(
    Math.floor(restartStartedAt.getTime() / 1_000) * 1_000,
  );

  const alarmsResponse = await request.get(`${apiUrl}/api/alarms`);
  expect(alarmsResponse.ok()).toBe(true);
  const alarms = await alarmsResponse.json() as Alarm[];
  expect(alarms.find((alarm) => alarm.id === activeBefore.id)).toMatchObject({
    id: activeBefore.id,
    deviceId: "AHU-204",
    status: "ACTIVE",
  });
});
