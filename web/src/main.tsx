import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AlarmsPage } from "./features/alarms/AlarmsPage";
import { EquipmentDetailPage } from "./features/equipment/EquipmentDetailPage";
import { EquipmentPage } from "./features/equipment/EquipmentPage";
import { OverviewPage } from "./features/overview/OverviewPage";
import { AppShell } from "./layout/AppShell";
import { TelemetryProvider } from "./hooks/useTelemetrySocket";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 10_000, retry: 1, refetchOnWindowFocus: false },
    mutations: { retry: false },
  },
});

const root = document.getElementById("root");
if (!root) throw new Error("Missing root element");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TelemetryProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<OverviewPage />} />
              <Route path="equipment" element={<EquipmentPage />} />
              <Route path="equipment/:id" element={<EquipmentDetailPage />} />
              <Route path="alarms" element={<AlarmsPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </TelemetryProvider>
    </QueryClientProvider>
  </StrictMode>,
);
