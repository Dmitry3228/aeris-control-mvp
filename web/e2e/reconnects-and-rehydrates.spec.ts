import { expect, test } from "@playwright/test";

const equipment = ["AHU-201", "AHU-204"].map((id, index) => ({
  id,
  name: `${id} handler`,
  equipmentType: "AHU",
  location: index === 0 ? "Main lobby" : "North production",
  enabled: true,
  operationalStatus: "RUNNING",
  state: {
    deviceId: id,
    timestamp: "2026-10-02T14:28:00Z",
    temperature: 21 + index,
    pressure: 5.2 + index,
    power: 12.6 + index,
    connected: true,
  },
}));

test("reconnects and rehydrates one latest row per device", async ({ page }) => {
  await page.route("**/api/health", (route) => route.fulfill({ json: { status: "ok", dependencies: { database: "ok" } } }));
  await page.route("**/api/equipment", (route) => route.fulfill({ json: equipment }));
  await page.route(/\/api\/equipment\/[^?]+/, (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-1) ?? "");
    return route.fulfill({ json: { ...equipment.find((item) => item.id === id), history: [] } });
  });
  await page.route("**/api/alarms", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/commands", (route) => route.fulfill({ json: [] }));

  let connection = 0;
  await page.routeWebSocket("**/api/ws/telemetry", async (webSocket) => {
    connection += 1;
    if (connection === 1) {
      webSocket.send(JSON.stringify({ type: "session", epoch: "epoch-a", sequence: 100 }));
      webSocket.send(JSON.stringify({ type: "telemetry", epoch: "epoch-a", sequence: 101, deviceId: "AHU-201", payload: { pressure: 5.4 } }));
      webSocket.send(JSON.stringify({ type: "telemetry", epoch: "epoch-a", sequence: 102, deviceId: "AHU-204", payload: { pressure: 8.0 } }));
      setTimeout(() => webSocket.close(), 50);
      return;
    }
    webSocket.send(JSON.stringify({ type: "session", epoch: "epoch-b", sequence: 0 }));
    webSocket.send(JSON.stringify({ type: "telemetry", epoch: "epoch-b", sequence: 1, deviceId: "AHU-201", payload: { pressure: 5.6 } }));
    webSocket.send(JSON.stringify({ type: "telemetry", epoch: "epoch-b", sequence: 2, deviceId: "AHU-204", payload: { pressure: 8.1 } }));
  });

  await page.goto("/");
  await expect.poll(() => connection).toBeGreaterThan(1);
  await expect(page.getByTestId("equipment-row")).toHaveCount(2);
  await expect(page.getByTestId("equipment-row").filter({ hasText: "AHU-201" })).toContainText("5.6 bar");
  await expect(page.getByTestId("equipment-row").filter({ hasText: "AHU-204" })).toContainText("8.1 bar");
});
