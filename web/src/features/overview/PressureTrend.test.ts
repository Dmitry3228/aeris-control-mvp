import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { createElement } from "react";

import type { TelemetryPoint } from "../../api/types";
import { PressureTrend, createPressureChartModel, getLiveTrendWindow, getTrendWindow } from "./PressureTrend";

const point = (timestamp: string, pressure: number): TelemetryPoint => ({
  timestamp,
  pressure,
  temperature: 21,
  power: 12,
  connected: true,
});

afterEach(() => vi.useRealTimers());

test("uses one shared time and pressure scale for path, ticks, threshold, and current marker", () => {
  const start = new Date("2026-10-03T08:00:00Z");
  const end = new Date("2026-10-03T09:00:00Z");
  const model = createPressureChartModel(
    [
      point("2026-10-03T08:00:00Z", 6),
      point("2026-10-03T08:30:00Z", 7.5),
      point("2026-10-03T09:00:00Z", 9),
    ],
    { start, end },
  );

  expect(model.points.map(({ x }) => x)).toEqual([36, 328, 620]);
  expect(model.points[1].y).toBe(model.thresholdY);
  expect(model.current).toEqual(model.points[2]);
  expect(model.yTicks.find((tick) => tick.value === 7.5)?.y).toBe(model.thresholdY);
  expect(model.xTicks[0].timestamp).toBe(start.getTime());
  expect(model.xTicks.at(-1)?.timestamp).toBe(end.getTime());
});

test.each([
  ["1H", 1],
  ["6H", 6],
  ["24H", 24],
  ["7D", 168],
] as const)("%s selects the matching equipment-history interval", (range, hours) => {
  const end = new Date("2026-10-03T09:00:00Z");
  const window = getTrendWindow(range, end);

  expect(window.end.toISOString()).toBe(end.toISOString());
  expect(window.end.getTime() - window.start.getTime()).toBe(hours * 60 * 60 * 1_000);
});

test("moves the visible window with wall time and future live samples", () => {
  const selectedEnd = new Date("2026-10-03T09:00:00Z");

  expect(getLiveTrendWindow("1H", selectedEnd, new Date("2026-10-03T09:00:12Z"), [])
    .end.toISOString()).toBe("2026-10-03T09:00:12.000Z");
  expect(getLiveTrendWindow("1H", selectedEnd, new Date("2026-10-03T09:00:12Z"), [
    point("2026-10-03T09:00:20Z", 6.8),
  ]).end.toISOString()).toBe("2026-10-03T09:00:20.000Z");
});

test("advances the visible live window without changing the history request", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-03T09:00:00Z"));
  const fetch = vi.fn<typeof globalThis.fetch>(() => Promise.resolve(new Response(
    JSON.stringify({
      id: "AHU-204",
      name: "Air Handler 204",
      equipmentType: "AHU",
      location: "Zone B",
      enabled: true,
      operationalStatus: "RUNNING",
      state: null,
      history: [],
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  )));
  vi.stubGlobal("fetch", fetch);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const equipment = {
    id: "AHU-204",
    name: "Air Handler 204",
    equipmentType: "AHU",
    location: "Zone B",
    enabled: true,
    operationalStatus: "RUNNING" as const,
    state: {
      deviceId: "AHU-204",
      timestamp: "2026-10-03T09:00:00Z",
      temperature: 21,
      pressure: 8,
      power: 12,
      connected: true,
    },
  };

  render(createElement(
    QueryClientProvider,
    { client: queryClient },
    createElement(PressureTrend, { equipment, liveHistory: [] }),
  ));
  const chart = screen.getByRole("img", { name: "AHU-204 pressure chart" });
  const visibleEnd = () => chart.parentElement?.querySelector(".chart-times time:last-child")?.getAttribute("datetime");
  expect(visibleEnd()).toBe("2026-10-03T09:00:00.000Z");
  expect(fetch).toHaveBeenCalledTimes(1);

  act(() => vi.advanceTimersByTime(2_000));

  expect(visibleEnd()).toBe("2026-10-03T09:00:02.000Z");
  expect(fetch).toHaveBeenCalledTimes(1);
});

test("keeps the approved 1h labels and stats after selecting a longer chart range", async () => {
  const now = Date.now();
  const timestamp = (minutesAgo: number) => new Date(now - minutesAgo * 60_000).toISOString();
  const history = [
    point(timestamp(180), 1),
    point(timestamp(90), 99),
    point(timestamp(50), 6.4),
    point(timestamp(10), 8.6),
  ];
  const equipment = {
    id: "AHU-204",
    name: "Air Handler 204",
    equipmentType: "AHU",
    location: "Zone B",
    enabled: true,
    operationalStatus: "RUNNING" as const,
    state: {
      deviceId: "AHU-204",
      timestamp: timestamp(0),
      temperature: 21,
      pressure: 8,
      power: 12,
      connected: true,
    },
  };
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
    JSON.stringify({ ...equipment, history }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  ))));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(createElement(
    QueryClientProvider,
    { client: queryClient },
    createElement(PressureTrend, { equipment, liveHistory: [] }),
  ));
  fireEvent.click(screen.getByRole("button", { name: "6H" }));

  expect(await screen.findByText("6.4 bar")).toBeVisible();
  expect(screen.getByText("Min (1h)")).toBeVisible();
  expect(screen.getByText("Max (1h)")).toBeVisible();
  expect(screen.getByText("8.6 bar")).toBeVisible();
  expect(screen.queryByText("Min (6h)")).not.toBeInTheDocument();
  expect(screen.queryByText("1.0 bar")).not.toBeInTheDocument();
  expect(screen.queryByText("99.0 bar")).not.toBeInTheDocument();
});
