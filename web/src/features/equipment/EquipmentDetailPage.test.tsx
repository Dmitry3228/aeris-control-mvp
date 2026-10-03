import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { EquipmentDetailPage } from "./EquipmentDetailPage";

test("shows server operational status, pressure history, and command history", async () => {
  vi.stubGlobal("WebSocket", class {
    addEventListener() {}
    close() {}
  });
  vi.stubGlobal("fetch", vi.fn<typeof fetch>((input) => {
    const url = String(input);
    if (url.includes("/api/commands")) {
      return Promise.resolve(new Response(JSON.stringify([{
        id: "command-1",
        deviceId: "AHU-204",
        kind: "START",
        value: null,
        status: "ACKNOWLEDGED",
        createdAt: "2026-10-03T08:59:00Z",
        timeoutAt: "2026-10-03T08:59:10Z",
        acknowledgedAt: "2026-10-03T08:59:01Z",
      }]), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    return Promise.resolve(new Response(JSON.stringify({
      id: "AHU-204",
      name: "Air Handler 204",
      equipmentType: "AHU",
      location: "Zone B",
      enabled: true,
      operationalStatus: "STALE",
      state: {
        deviceId: "AHU-204",
        timestamp: "2026-10-03T08:55:00Z",
        temperature: 21,
        pressure: 7.7,
        power: 12,
        connected: true,
      },
      history: [{
        timestamp: "2026-10-03T08:55:00Z",
        temperature: 21,
        pressure: 7.7,
        power: 12,
        connected: true,
      }],
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
  }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/equipment/AHU-204"]}>
        <Routes>
          <Route path="/equipment/:id" element={<EquipmentDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", { name: "AHU-204" })).toBeVisible();
  expect(screen.getByText("Stale")).toBeVisible();
  expect(screen.getByRole("img", { name: "AHU-204 pressure chart" })).toBeVisible();
  expect(await screen.findByText("Acknowledged")).toBeVisible();
});
