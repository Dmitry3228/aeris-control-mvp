import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { AlarmsPage } from "./AlarmsPage";

const activeAlarm = {
  id: "00000000-0000-0000-0000-000000000204",
  deviceId: "AHU-204",
  ruleKey: "pressure_high",
  status: "ACTIVE",
  severity: "WARNING",
  message: "High discharge pressure",
  openedAt: "2026-10-02T14:28:00Z",
  updatedAt: "2026-10-02T14:28:00Z",
  acknowledgedAt: null,
  closedAt: null,
} as const;

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AlarmsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("acknowledging an alarm calls the API and visibly updates its row", async () => {
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      new Response(JSON.stringify([activeAlarm]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ...activeAlarm,
          status: "ACKNOWLEDGED",
          acknowledgedAt: "2026-10-02T14:30:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify([{ ...activeAlarm, status: "ACKNOWLEDGED" }]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();

  renderPage();
  expect(await screen.findByLabelText("Alarm measurement unavailable")).toHaveTextContent("—");
  expect(screen.queryByText("pressure high", { exact: false })).not.toBeInTheDocument();
  await user.click(await screen.findByRole("button", { name: "Acknowledge AHU-204 alarm" }));

  await waitFor(() => expect(screen.getByText("Acknowledged")).toBeVisible());
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/alarms/00000000-0000-0000-0000-000000000204/ack",
    expect.objectContaining({ method: "POST" }),
  );
  expect(screen.queryByRole("button", { name: "Acknowledge AHU-204 alarm" })).not.toBeInTheDocument();
});

test("shows acknowledgement problem detail beside the affected alarm action", async () => {
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      new Response(JSON.stringify([activeAlarm]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Command unavailable",
          status: 503,
          detail: "Alarm acknowledgement could not be published",
          instance: `/api/alarms/${activeAlarm.id}/ack`,
        }),
        { status: 503, headers: { "Content-Type": "application/problem+json" } },
      ),
    );
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();

  renderPage();
  await user.click(await screen.findByRole("button", { name: "Acknowledge AHU-204 alarm" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Alarm acknowledgement could not be published");
  expect(screen.getByRole("button", { name: "Acknowledge AHU-204 alarm" })).toBeVisible();
});

test("renders distinct active acknowledged and closed lifecycle states", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify([
          activeAlarm,
          { ...activeAlarm, id: "alarm-ack", deviceId: "AHU-205", status: "ACKNOWLEDGED" },
          { ...activeAlarm, id: "alarm-closed", deviceId: "AHU-206", status: "CLOSED" },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );

  renderPage();

  expect(await screen.findByText("Active", { exact: true })).toBeVisible();
  expect(screen.getByText("Acknowledged", { exact: true })).toBeVisible();
  expect(screen.getByText("Closed", { exact: true })).toBeVisible();
  expect(document.querySelector('[data-alarm-status="ACTIVE"]')).toBeTruthy();
  expect(document.querySelector('[data-alarm-status="ACKNOWLEDGED"]')).toBeTruthy();
  expect(document.querySelector('[data-alarm-status="CLOSED"]')).toBeTruthy();
});
