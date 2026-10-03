import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api } from "../../api/client";
import type { EquipmentSummary, TelemetryPoint } from "../../api/types";

const CHART_WIDTH = 620;
const CHART_HEIGHT = 180;
const PLOT_LEFT = 36;
const PRESSURE_THRESHOLD = 7.5;

export type TrendRange = "1H" | "6H" | "24H" | "7D";

const RANGE_MILLISECONDS: Record<TrendRange, number> = {
  "1H": 60 * 60 * 1_000,
  "6H": 6 * 60 * 60 * 1_000,
  "24H": 24 * 60 * 60 * 1_000,
  "7D": 7 * 24 * 60 * 60 * 1_000,
};

export interface TrendWindow {
  start: Date;
  end: Date;
}

export interface ChartPoint {
  timestamp: number;
  pressure: number;
  x: number;
  y: number;
}

export interface PressureChartModel {
  points: ChartPoint[];
  path: string;
  current: ChartPoint | null;
  thresholdY: number;
  yTicks: Array<{ value: number; y: number }>;
  xTicks: Array<{ timestamp: number; x: number; label: string }>;
}

export function getTrendWindow(range: TrendRange, end = new Date()): TrendWindow {
  return {
    start: new Date(end.getTime() - RANGE_MILLISECONDS[range]),
    end: new Date(end),
  };
}

export function getLiveTrendWindow(
  range: TrendRange,
  selectedEnd: Date,
  now: Date,
  points: TelemetryPoint[],
): TrendWindow {
  const latestPoint = points.reduce((latest, point) => {
    const timestamp = new Date(point.timestamp).getTime();
    return Number.isFinite(timestamp) ? Math.max(latest, timestamp) : latest;
  }, Number.NEGATIVE_INFINITY);
  const end = new Date(Math.max(selectedEnd.getTime(), now.getTime(), latestPoint));
  return getTrendWindow(range, end);
}

function formatTimeTick(timestamp: number, rangeMilliseconds: number) {
  const options: Intl.DateTimeFormatOptions = rangeMilliseconds > RANGE_MILLISECONDS["24H"]
    ? { month: "short", day: "numeric" }
    : { hour: "2-digit", minute: "2-digit", hour12: false };
  return new Intl.DateTimeFormat("en", options).format(new Date(timestamp));
}

export function createPressureChartModel(
  source: TelemetryPoint[],
  window: TrendWindow,
): PressureChartModel {
  const start = window.start.getTime();
  const end = window.end.getTime();
  const duration = Math.max(1, end - start);
  const samples = source
    .map((point) => ({ ...point, time: new Date(point.timestamp).getTime() }))
    .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.pressure) && point.time >= start && point.time <= end)
    .sort((left, right) => left.time - right.time);
  const pressures = [PRESSURE_THRESHOLD, ...samples.map((point) => point.pressure)];
  let domainMinimum = Math.floor(Math.min(...pressures) * 2) / 2;
  let domainMaximum = Math.ceil(Math.max(...pressures) * 2) / 2;
  if (domainMinimum === domainMaximum) {
    domainMinimum -= 1;
    domainMaximum += 1;
  }
  const pressureSpan = domainMaximum - domainMinimum;
  const xScale = (timestamp: number) => PLOT_LEFT + ((timestamp - start) / duration) * (CHART_WIDTH - PLOT_LEFT);
  const yScale = (pressure: number) => CHART_HEIGHT - ((pressure - domainMinimum) / pressureSpan) * CHART_HEIGHT;
  const points = samples.map<ChartPoint>((point) => ({
    timestamp: point.time,
    pressure: point.pressure,
    x: Number(xScale(point.time).toFixed(2)),
    y: Number(yScale(point.pressure).toFixed(2)),
  }));
  const yTicks = Array.from({ length: 5 }, (_, index) => {
    const value = domainMinimum + (pressureSpan * index) / 4;
    return { value, y: Number(yScale(value).toFixed(2)) };
  }).reverse();
  const xTicks = Array.from({ length: 4 }, (_, index) => {
    const timestamp = start + (duration * index) / 3;
    return {
      timestamp,
      x: Number(xScale(timestamp).toFixed(2)),
      label: formatTimeTick(timestamp, duration),
    };
  });

  return {
    points,
    path: points.map((point, index) => `${index === 0 ? "M" : "L"}${point.x} ${point.y}`).join(" "),
    current: points.at(-1) ?? null,
    thresholdY: Number(yScale(PRESSURE_THRESHOLD).toFixed(2)),
    yTicks,
    xTicks,
  };
}

function mergePoints(...collections: Array<TelemetryPoint[] | undefined>) {
  const byTimestamp = new Map<string, TelemetryPoint>();
  for (const collection of collections) {
    for (const point of collection ?? []) byTimestamp.set(point.timestamp, point);
  }
  return Array.from(byTimestamp.values());
}

interface PressureTrendProps {
  equipment: EquipmentSummary | undefined;
  liveHistory: TelemetryPoint[];
}

export function PressureTrend({ equipment, liveHistory }: PressureTrendProps) {
  const [selection, setSelection] = useState<{ range: TrendRange; end: Date }>(() => ({ range: "1H", end: new Date() }));
  const [now, setNow] = useState(() => new Date());
  const requestWindow = useMemo(() => getTrendWindow(selection.range, selection.end), [selection]);
  const history = useQuery({
    queryKey: ["equipment-history", equipment?.id, selection.range, requestWindow.start.toISOString(), requestWindow.end.toISOString()],
    queryFn: () => api.equipmentDetail(equipment!.id, requestWindow.start.toISOString(), requestWindow.end.toISOString()),
    enabled: Boolean(equipment),
  });
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(timer);
  }, []);
  const points = useMemo(() => {
    const statePoint = equipment?.state ? [{ ...equipment.state }] : undefined;
    return mergePoints(history.data?.history, statePoint, liveHistory);
  }, [equipment?.state, history.data?.history, liveHistory]);
  const visibleWindow = useMemo(
    () => getLiveTrendWindow(selection.range, selection.end, now, points),
    [now, points, selection],
  );
  const model = useMemo(() => createPressureChartModel(points, visibleWindow), [points, visibleWindow]);
  const latestTimestamp = model.current?.timestamp;
  const pressures = model.points
    .filter((point) => latestTimestamp !== undefined && point.timestamp >= latestTimestamp - RANGE_MILLISECONDS["1H"])
    .map((point) => point.pressure);
  const minimum = pressures.length > 0 ? Math.min(...pressures) : undefined;
  const maximum = pressures.length > 0 ? Math.max(...pressures) : undefined;

  return (
    <section className="panel trend-panel">
      <header className="panel-heading trend-heading">
        <h2>{equipment?.id ?? "Equipment"} — Pressure trend</h2>
        <div className="range-control" aria-label="Pressure trend range">
          {(["1H", "6H", "24H", "7D"] as const).map((option) => (
            <button
              aria-pressed={selection.range === option}
              className={selection.range === option ? "is-active" : ""}
              key={option}
              onClick={() => {
                const nextNow = new Date();
                setNow(nextNow);
                setSelection({ range: option, end: nextNow });
              }}
              type="button"
            >
              {option}
            </button>
          ))}
        </div>
      </header>
      <div className="trend-body">
        <div className="chart-wrap">
          <span className="chart-axis-label">Pressure (bar)</span>
          <svg aria-label={`${equipment?.id ?? "Equipment"} pressure chart`} className="pressure-chart" preserveAspectRatio="none" role="img" viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}>
            {model.yTicks.map((tick) => <line className="chart-grid" key={`y-${tick.value}`} x1={PLOT_LEFT} x2={CHART_WIDTH} y1={tick.y} y2={tick.y} />)}
            {model.xTicks.map((tick) => <line className="chart-grid" key={`x-${tick.timestamp}`} x1={tick.x} x2={tick.x} y1="0" y2={CHART_HEIGHT} />)}
            {model.yTicks.map((tick) => <text className="chart-tick" key={`label-${tick.value}`} x="1" y={tick.y + 5}>{tick.value.toFixed(1)}</text>)}
            <line className="threshold-line" data-pressure={PRESSURE_THRESHOLD} x1={PLOT_LEFT} x2={CHART_WIDTH} y1={model.thresholdY} y2={model.thresholdY} />
            {model.path ? <path className="telemetry-line" d={model.path} vectorEffect="non-scaling-stroke" /> : null}
            {model.current ? <circle className="current-marker" cx={model.current.x} cy={model.current.y} data-pressure={model.current.pressure} r="4" vectorEffect="non-scaling-stroke" /> : null}
          </svg>
          <div className="chart-times">{model.xTicks.map((tick) => <time dateTime={new Date(tick.timestamp).toISOString()} key={tick.timestamp}>{tick.label}</time>)}</div>
        </div>
        <dl className="trend-stats">
          <div><dt>Current</dt><dd className="is-accent">{model.current ? `${model.current.pressure.toFixed(1)} bar` : "—"}</dd></div>
          <div><dt>Threshold</dt><dd className="is-warning">{PRESSURE_THRESHOLD.toFixed(1)} bar</dd></div>
          <div><dt>Min (1h)</dt><dd>{minimum === undefined ? "—" : `${minimum.toFixed(1)} bar`}</dd></div>
          <div><dt>Max (1h)</dt><dd>{maximum === undefined ? "—" : `${maximum.toFixed(1)} bar`}</dd></div>
        </dl>
      </div>
    </section>
  );
}
