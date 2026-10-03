import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import type { PropsWithChildren } from "react";

import type { Alarm, Command, EquipmentDetail, EquipmentSummary } from "../api/types";
import { TelemetryProvider, useTelemetry } from "./useTelemetrySocket";

type Listener = (event: { data?: string }) => void;

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  private listeners = new Map<string, Listener[]>();

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, listener: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  open() {
    this.emit("open");
  }

  message(payload: object) {
    this.emit("message", { data: JSON.stringify(payload) });
  }

  close() {
    this.emit("close");
  }

  private emit(type: string, event: { data?: string } = {}) {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

const equipment: EquipmentSummary = {
  id: "AHU-204",
  name: "North production air handler",
  equipmentType: "AHU",
  location: "North production",
  enabled: true,
  operationalStatus: "RUNNING",
  state: {
    deviceId: "AHU-204",
    timestamp: "2026-10-03T08:00:00Z",
    temperature: 21.4,
    pressure: 7.2,
    power: 3.2,
    connected: true,
  },
};

const alarm: Alarm = {
  id: "alarm-1",
  deviceId: "AHU-204",
  ruleKey: "pressure_high",
  status: "ACTIVE",
  severity: "WARNING",
  message: "High pressure",
  openedAt: "2026-10-03T08:00:00Z",
  updatedAt: "2026-10-03T08:00:00Z",
  acknowledgedAt: null,
  closedAt: null,
};

const command: Command = {
  id: "command-1",
  deviceId: "AHU-204",
  kind: "START",
  value: null,
  status: "PENDING",
  createdAt: "2026-10-03T08:00:00Z",
  timeoutAt: "2026-10-03T08:00:10Z",
  acknowledgedAt: null,
};

function Probe() {
  const telemetry = useTelemetry();
  return (
    <span data-testid="socket-state">
      {telemetry.connected ? "connected" : "disconnected"}:
      {telemetry.latest["AHU-204"]?.sequence ?? "none"}
    </span>
  );
}

function renderProvider(queryClient: QueryClient) {
  function Wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={queryClient}>
        <TelemetryProvider>{children}</TelemetryProvider>
      </QueryClientProvider>
    );
  }
  return render(<Probe />, { wrapper: Wrapper });
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("reconciles every cache and accepts lower sequence after backend epoch changes", () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const serverHistory = [
    { timestamp: "2026-10-03T07:00:00Z", temperature: 21, pressure: 7.1, power: 3, connected: true },
    { timestamp: "2026-10-03T08:00:00Z", temperature: 21.4, pressure: 7.2, power: 3.2, connected: true },
  ];
  queryClient.setQueryData<EquipmentSummary[]>(["equipment"], [equipment]);
  queryClient.setQueryData<EquipmentDetail>(["equipment", "AHU-204"], {
    ...equipment,
    history: serverHistory,
  });
  queryClient.setQueryData<EquipmentDetail>(["equipment-history", "AHU-204", "7D"], {
    ...equipment,
    history: serverHistory,
  });
  queryClient.setQueryData<Alarm[]>(["alarms"], [alarm]);
  queryClient.setQueryData<Command[]>(["commands"], [command]);
  queryClient.setQueryData<Command[]>(["commands", "AHU-204"], [command]);

  renderProvider(queryClient);
  const first = FakeWebSocket.instances[0];
  act(() => {
    first.open();
    first.message({ type: "session", epoch: "epoch-a", sequence: 90 });
    first.message({
      type: "telemetry",
      epoch: "epoch-a",
      sequence: 91,
      deviceId: "AHU-204",
      payload: {
        timestamp: "2026-10-03T08:00:01Z",
        temperature: 21.5,
        pressure: 7.8,
        power: 3.3,
        connected: true,
      },
    });
    first.message({
      type: "alarm",
      epoch: "epoch-a",
      sequence: 92,
      deviceId: "AHU-204",
      payload: { ...alarm, status: "CLOSED", action: "CLOSE", closedAt: "2026-10-03T08:00:01Z" },
    });
    first.message({
      type: "command",
      epoch: "epoch-a",
      sequence: 93,
      deviceId: "AHU-204",
      payload: { ...command, status: "ACKNOWLEDGED", acknowledgedAt: "2026-10-03T08:00:02Z" },
    });
  });

  expect(screen.getByTestId("socket-state")).toHaveTextContent("connected:91");
  expect(queryClient.getQueryData<EquipmentSummary[]>(["equipment"])?.[0].state?.pressure).toBe(7.8);
  expect(queryClient.getQueryData<EquipmentDetail>(["equipment", "AHU-204"])?.state?.pressure).toBe(7.8);
  expect(queryClient.getQueryData<EquipmentDetail>(["equipment", "AHU-204"])?.history).toEqual(serverHistory);
  expect(queryClient.getQueryData<EquipmentDetail>(["equipment-history", "AHU-204", "7D"])?.history).toEqual(serverHistory);
  expect(queryClient.getQueryData<Alarm[]>(["alarms"])?.[0].status).toBe("CLOSED");
  expect(queryClient.getQueryData<Command[]>(["commands"])?.[0].status).toBe("ACKNOWLEDGED");
  expect(queryClient.getQueryData<Command[]>(["commands", "AHU-204"])?.[0].status).toBe("ACKNOWLEDGED");

  act(() => {
    first.close();
    vi.advanceTimersByTime(250);
  });
  const restarted = FakeWebSocket.instances[1];
  act(() => {
    restarted.open();
    restarted.message({ type: "session", epoch: "epoch-b", sequence: 0 });
    restarted.message({
      type: "telemetry",
      epoch: "epoch-b",
      sequence: 1,
      deviceId: "AHU-204",
      payload: { timestamp: "2026-10-03T08:00:03Z", pressure: 6.4, connected: true },
    });
  });

  expect(screen.getByTestId("socket-state")).toHaveTextContent("connected:1");
  expect(queryClient.getQueryData<EquipmentSummary[]>(["equipment"])?.[0].state?.pressure).toBe(6.4);
  expect(queryClient.getQueryState(["alarms"])?.isInvalidated).toBe(true);
});

test("retries forever with a bounded delay after an extended outage", () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderProvider(queryClient);

  for (let failure = 0; failure < 12; failure += 1) {
    const socket = FakeWebSocket.instances.at(-1);
    expect(socket).toBeDefined();
    act(() => {
      socket?.close();
      vi.advanceTimersByTime(5_000);
    });
  }

  expect(FakeWebSocket.instances).toHaveLength(13);
});
