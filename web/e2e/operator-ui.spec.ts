import { expect, test, type Page } from "@playwright/test";

const equipment = [
  ["AHU-201", "Main lobby", 5.2, 21.1, 12.6],
  ["AHU-202", "Office west", 5.8, 22.0, 14.1],
  ["AHU-203", "Office east", 6.1, 21.8, 13.9],
  ["AHU-204", "North production", 8.1, 23.4, 18.2],
  ["AHU-205", "South production", 5.6, 22.1, 15.0],
  ["AHU-206", "Packaging", 4.9, 20.4, 11.8],
  ["AHU-207", "Warehouse", 5.1, 19.8, 11.2],
  ["AHU-208", "Utilities", 6.3, 21.9, 16.4],
  ["AHU-209", "Laboratory", 5.7, 22.3, 14.8],
  ["AHU-210", "Data center", 5.9, 20.1, 13.6],
  ["AHU-211", "Cafeteria", 4.8, 21.4, 11.9],
  ["AHU-212", "Maintenance", 5.3, 20.9, 12.4],
].map(([id, location, pressure, temperature, power]) => ({
  id,
  name: `${id} air handler`,
  equipmentType: "AHU",
  location,
  enabled: true,
  operationalStatus: "RUNNING",
  state: { deviceId: id, timestamp: "2025-04-28T14:28:00Z", temperature, pressure, power, connected: true },
}));

const alarms = [
  ["alarm-204", "AHU-204", "High discharge pressure", "2025-04-28T14:28:00Z"],
  ["alarm-ch-001", "CH-001", "High motor temperature", "2025-04-28T13:17:00Z"],
  ["alarm-107", "AHU-107", "Filter differential pressure high", "2025-04-28T11:03:00Z"],
].map(([id, deviceId, message, updatedAt]) => ({
  id, deviceId, ruleKey: "pressure_high", status: "ACTIVE", severity: "WARNING", message,
  openedAt: updatedAt, updatedAt, acknowledgedAt: null, closedAt: null,
}));

let commands = [
  { id: "cmd-1", deviceId: "AHU-204", kind: "SETPOINT", value: 21, status: "ACKNOWLEDGED", createdAt: "2025-04-28T14:29:00Z", timeoutAt: "2025-04-28T14:29:10Z", acknowledgedAt: "2025-04-28T14:29:02Z" },
  { id: "cmd-2", deviceId: "CH-001", kind: "SETPOINT", value: 7, status: "PENDING", createdAt: "2025-04-28T13:12:00Z", timeoutAt: "2025-04-28T13:12:10Z", acknowledgedAt: null },
  { id: "cmd-3", deviceId: "AHU-201", kind: "START", value: null, status: "ACKNOWLEDGED", createdAt: "2025-04-28T10:41:00Z", timeoutAt: "2025-04-28T10:41:10Z", acknowledgedAt: "2025-04-28T10:41:02Z" },
  { id: "cmd-4", deviceId: "AHU-203", kind: "START", value: null, status: "ACKNOWLEDGED", createdAt: "2025-04-28T08:16:00Z", timeoutAt: "2025-04-28T08:16:10Z", acknowledgedAt: "2025-04-28T08:16:02Z" },
];

async function installApi(page: Page, historyRequests: URL[]) {
  await page.route("**/api/health", (route) => route.fulfill({ json: { status: "ok", dependencies: { database: "ok" } } }));
  await page.route("**/api/equipment", (route) => route.fulfill({ json: equipment }));
  await page.route(/\/api\/equipment\/[^?]+/, (route) => {
    const requestUrl = new URL(route.request().url());
    const id = decodeURIComponent(requestUrl.pathname.split("/").at(-1) ?? "");
    if (requestUrl.searchParams.has("start")) historyRequests.push(requestUrl);
    const item = equipment.find((candidate) => candidate.id === id);
    const start = new Date(requestUrl.searchParams.get("start") ?? "2025-04-28T13:30:00Z").getTime();
    const end = new Date(requestUrl.searchParams.get("end") ?? "2025-04-28T14:30:00Z").getTime();
    const history = Array.from({ length: 32 }, (_, index) => ({
      timestamp: new Date(start + ((end - start) * index) / 31).toISOString(),
      pressure: 6 + index * .065 + Math.sin(index * 1.7) * .12,
      temperature: 23.4,
      power: 18.2,
      connected: true,
    }));
    return route.fulfill({ json: { ...item, history } });
  });
  await page.route("**/api/alarms", (route) => route.fulfill({ json: alarms }));
  await page.route(/\/api\/alarms\/[^/]+\/ack$/, async (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-2);
    const alarm = alarms.find((candidate) => candidate.id === id)!;
    Object.assign(alarm, { status: "ACKNOWLEDGED", acknowledgedAt: "2025-04-28T14:32:00Z" });
    return route.fulfill({ json: alarm });
  });
  await page.route("**/api/commands**", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      const created = { id: "cmd-new", ...body, value: body.value ?? null, status: "PENDING", createdAt: "2025-04-28T14:32:00Z", timeoutAt: "2025-04-28T14:32:10Z", acknowledgedAt: null };
      commands = [created, ...commands];
      return route.fulfill({ status: 201, json: created });
    }
    return route.fulfill({ json: commands });
  });
  await page.routeWebSocket("**/api/ws/telemetry", (socket) => {
    socket.send(JSON.stringify({ type: "session", epoch: "mock-operator", sequence: 0 }));
    for (let sequence = 1; sequence <= 32; sequence += 1) {
      socket.send(JSON.stringify({
        type: "telemetry",
        epoch: "mock-operator",
        sequence,
        deviceId: "AHU-204",
        payload: { timestamp: `2025-04-28T14:${String(sequence).padStart(2, "0")}:00Z`, pressure: 6 + sequence * .065 + Math.sin(sequence * 1.7) * .12, temperature: 23.4, power: 18.2, connected: true },
      }));
    }
  });
}

test("operator overview is responsive and core interactions use the API", async ({ page }) => {
  const consoleErrors: string[] = [];
  const historyRequests: URL[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await installApi(page, historyRequests);

  await page.setViewportSize({ width: 1536, height: 1024 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Live operations" })).toBeVisible();
  await expect(page.getByTestId("equipment-row")).toHaveCount(12);
  await expect.poll(() => historyRequests.length).toBeGreaterThanOrEqual(1);
  expect(new Date(historyRequests[0].searchParams.get("end")!).getTime() - new Date(historyRequests[0].searchParams.get("start")!).getTime()).toBe(60 * 60 * 1_000);
  await page.getByRole("button", { name: "6H" }).click();
  await expect.poll(() => historyRequests.length).toBeGreaterThanOrEqual(2);
  expect(new Date(historyRequests[1].searchParams.get("end")!).getTime() - new Date(historyRequests[1].searchParams.get("start")!).getTime()).toBe(6 * 60 * 60 * 1_000);
  await page.getByRole("button", { name: "1H" }).click();
  await expect.poll(() => historyRequests.length).toBeGreaterThanOrEqual(3);
  await expect(page.locator(".telemetry-line")).toBeVisible();
  await expect(page.locator(".current-marker")).toBeVisible();
  await page.screenshot({ path: "../.superpowers/sdd/2026-10-02-aeris-control-implementation/task-5-qa/desktop-latest.png" });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Live operations" })).toBeVisible();
  const mobileIdCell = page.getByTestId("equipment-row").filter({ hasText: "AHU-204" }).locator("td").first();
  await expect(mobileIdCell).toHaveText("AHU-204");
  expect(await mobileIdCell.evaluate((cell) => cell.scrollWidth <= cell.clientWidth)).toBe(true);
  await page.screenshot({ path: "../.superpowers/sdd/2026-10-02-aeris-control-implementation/task-5-qa/mobile-latest.png" });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);

  await page.getByRole("button", { name: "Acknowledge AHU-204 alarm" }).click();
  await expect(page.getByRole("button", { name: "Acknowledge AHU-204 alarm" })).toHaveCount(0);
  await page.getByRole("link", { name: "Equipment", exact: true }).last().click();
  await page.getByRole("searchbox", { name: "Search equipment" }).fill("Utilities");
  await expect(page.getByTestId("equipment-row")).toHaveCount(1);
  await page.getByRole("link", { name: "View AHU-208" }).click();
  await page.getByRole("combobox").selectOption("SETPOINT");
  await page.getByRole("spinbutton").fill("22.5");
  await page.getByRole("button", { name: "Send command" }).click();
  await expect(page.getByText("Pending").first()).toBeVisible();
  expect(consoleErrors).toEqual([]);
});
