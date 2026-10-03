import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { EquipmentPage } from "./EquipmentPage";

const equipment = [
  {
    id: "AHU-201",
    name: "Lobby handler",
    equipmentType: "AHU",
    location: "Main lobby",
    enabled: true,
    operationalStatus: "RUNNING",
    state: { deviceId: "AHU-201", timestamp: "2026-10-02T14:28:00Z", temperature: 21.1, pressure: 5.2, power: 12.6, connected: true },
  },
  {
    id: "CH-001",
    name: "Water chiller",
    equipmentType: "CHILLER",
    location: "Utilities",
    enabled: true,
    operationalStatus: "STALE",
    state: { deviceId: "CH-001", timestamp: "2026-10-02T14:28:00Z", temperature: 8.1, pressure: 3.8, power: 18.2, connected: true },
  },
];

test("equipment search and type filters reduce the visible rows", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(equipment), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <EquipmentPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByText("AHU-201")).toBeVisible();
  expect(screen.getByText("CH-001")).toBeVisible();
  expect(screen.getByText("Stale")).toBeVisible();

  await user.type(screen.getByRole("searchbox", { name: "Search equipment" }), "Utilities");
  expect(screen.queryByText("AHU-201")).not.toBeInTheDocument();
  expect(screen.getByText("CH-001")).toBeVisible();

  await user.clear(screen.getByRole("searchbox", { name: "Search equipment" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "Equipment type" }), "AHU");
  expect(screen.getByText("AHU-201")).toBeVisible();
  expect(screen.queryByText("CH-001")).not.toBeInTheDocument();
});
