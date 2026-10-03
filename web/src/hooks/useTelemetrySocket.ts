import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  createElement,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type {
  Alarm,
  AlarmEnvelope,
  Command,
  CommandEnvelope,
  EquipmentDetail,
  EquipmentSummary,
  SessionEnvelope,
  StreamEnvelope,
  TelemetryEnvelope,
  TelemetryPoint,
} from "../api/types";

const BASE_RECONNECT_MS = 250;
const MAX_RECONNECT_MS = 5_000;
const MAX_HISTORY_POINTS = 80;
const STALE_RECONCILE_MS = 5_000;

export interface TelemetrySnapshot {
  epoch: string;
  sequence: number;
  point: TelemetryPoint;
}

export interface TelemetrySocketState {
  connected: boolean;
  latest: Readonly<Record<string, TelemetrySnapshot>>;
  history: Readonly<Record<string, TelemetryPoint[]>>;
}

const EMPTY_STATE: TelemetrySocketState = {
  connected: false,
  latest: {},
  history: {},
};

const TelemetryContext = createContext<TelemetrySocketState | null>(null);

function isBaseEnvelope(value: unknown): value is {
  type: string;
  epoch: string;
  sequence: number;
} {
  if (!value || typeof value !== "object") return false;
  const candidate = value as { type?: unknown; epoch?: unknown; sequence?: unknown };
  return (
    typeof candidate.type === "string"
    && typeof candidate.epoch === "string"
    && typeof candidate.sequence === "number"
  );
}

function isSessionEnvelope(value: unknown): value is SessionEnvelope {
  return isBaseEnvelope(value) && value.type === "session";
}

function isDataEnvelope(value: unknown): value is Exclude<StreamEnvelope, SessionEnvelope> {
  if (!isBaseEnvelope(value)) return false;
  const candidate = value as Partial<Exclude<StreamEnvelope, SessionEnvelope>>;
  return (
    (candidate.type === "telemetry" || candidate.type === "alarm" || candidate.type === "command")
    && typeof candidate.deviceId === "string"
    && typeof candidate.payload === "object"
    && candidate.payload !== null
  );
}

function websocketUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/ws/telemetry`;
}

function pointFrom(envelope: TelemetryEnvelope, fallback?: EquipmentSummary["state"]): TelemetryPoint {
  return {
    timestamp: envelope.payload.timestamp ?? fallback?.timestamp ?? new Date().toISOString(),
    temperature: envelope.payload.temperature ?? fallback?.temperature ?? Number.NaN,
    pressure: envelope.payload.pressure ?? fallback?.pressure ?? Number.NaN,
    power: envelope.payload.power ?? fallback?.power ?? Number.NaN,
    connected: envelope.payload.connected ?? fallback?.connected ?? true,
  };
}

function mergeEquipment(
  equipment: EquipmentSummary,
  envelope: TelemetryEnvelope,
): EquipmentSummary {
  if (equipment.id !== envelope.deviceId) return equipment;
  const point = pointFrom(envelope, equipment.state);
  return {
    ...equipment,
    operationalStatus: point.connected ? "RUNNING" : "OFFLINE",
    state: { deviceId: equipment.id, ...point },
  };
}

function upsertById<T extends { id: string }>(items: T[] | undefined, update: T): T[] {
  if (!items) return [update];
  const present = items.some((item) => item.id === update.id);
  return present
    ? items.map((item) => (item.id === update.id ? update : item))
    : [update, ...items];
}

export function reconnectDelay(attempt: number): number {
  return Math.min(BASE_RECONNECT_MS * 2 ** attempt, MAX_RECONNECT_MS);
}

function useTelemetryConnection(): TelemetrySocketState {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [latest, setLatest] = useState<Record<string, TelemetrySnapshot>>({});
  const [history, setHistory] = useState<Record<string, TelemetryPoint[]>>({});
  const epoch = useRef<string | null>(null);
  const sequenceByStream = useRef(new Map<string, number>());

  const reconcile = useCallback(() => {
    void queryClient.invalidateQueries({
      predicate: (query) => {
        const root = query.queryKey[0];
        return root === "equipment" || root === "equipment-history" || root === "alarms" || root === "commands";
      },
    });
  }, [queryClient]);

  const establishEpoch = useCallback((nextEpoch: string) => {
    if (epoch.current === nextEpoch) return;
    epoch.current = nextEpoch;
    sequenceByStream.current.clear();
    setLatest({});
    setHistory({});
    reconcile();
  }, [reconcile]);

  const mergeTelemetry = useCallback((envelope: TelemetryEnvelope) => {
    const point = pointFrom(envelope);
    setLatest((current) => ({
      ...current,
      [envelope.deviceId]: {
        epoch: envelope.epoch,
        sequence: envelope.sequence,
        point,
      },
    }));
    setHistory((current) => ({
      ...current,
      [envelope.deviceId]: [...(current[envelope.deviceId] ?? []), point].slice(-MAX_HISTORY_POINTS),
    }));
    queryClient.setQueryData<EquipmentSummary[]>(["equipment"], (current) =>
      current?.map((item) => mergeEquipment(item, envelope)),
    );
    queryClient.setQueryData<EquipmentDetail>(["equipment", envelope.deviceId], (current) => {
      if (!current) return current;
      const merged = mergeEquipment(current, envelope);
      return {
        ...merged,
        history: current.history,
      };
    });
    queryClient.setQueriesData<EquipmentDetail>(
      { queryKey: ["equipment-history", envelope.deviceId] },
      (current) => current
        ? {
            ...mergeEquipment(current, envelope),
            history: current.history,
          }
        : current,
    );
  }, [queryClient]);

  const mergeAlarm = useCallback((envelope: AlarmEnvelope) => {
    queryClient.setQueryData<Alarm[]>(["alarms"], (current) =>
      upsertById(current, envelope.payload),
    );
    void queryClient.invalidateQueries({ queryKey: ["alarms"] });
  }, [queryClient]);

  const mergeCommand = useCallback((envelope: CommandEnvelope) => {
    queryClient.setQueriesData<Command[]>({ queryKey: ["commands"] }, (current) =>
      upsertById(current, envelope.payload),
    );
    void queryClient.invalidateQueries({ queryKey: ["commands"] });
  }, [queryClient]);

  const merge = useCallback((envelope: Exclude<StreamEnvelope, SessionEnvelope>) => {
    establishEpoch(envelope.epoch);
    const streamKey = `${envelope.type}:${envelope.deviceId}`;
    const previousSequence = sequenceByStream.current.get(streamKey) ?? -1;
    if (envelope.sequence <= previousSequence) return;
    sequenceByStream.current.set(streamKey, envelope.sequence);
    if (envelope.type === "telemetry") mergeTelemetry(envelope);
    if (envelope.type === "alarm") mergeAlarm(envelope);
    if (envelope.type === "command") mergeCommand(envelope);
  }, [establishEpoch, mergeAlarm, mergeCommand, mergeTelemetry]);

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let attempt = 0;

    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(websocketUrl());
      socket.addEventListener("open", () => {
        setConnected(true);
        attempt = 0;
        reconcile();
      });
      socket.addEventListener("message", (event) => {
        try {
          const parsed: unknown = JSON.parse(String(event.data));
          if (isSessionEnvelope(parsed)) {
            establishEpoch(parsed.epoch);
          } else if (isDataEnvelope(parsed)) {
            merge(parsed);
          }
        } catch {
          // Malformed frames are isolated; the durable REST reconciliation remains authoritative.
        }
      });
      socket.addEventListener("close", () => {
        setConnected(false);
        if (disposed) return;
        const delay = reconnectDelay(attempt);
        attempt += 1;
        reconnectTimer = window.setTimeout(connect, delay);
      });
      socket.addEventListener("error", () => socket?.close());
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [establishEpoch, merge, reconcile]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void queryClient.invalidateQueries({ queryKey: ["equipment"] });
    }, STALE_RECONCILE_MS);
    return () => window.clearInterval(timer);
  }, [queryClient]);

  return useMemo(() => ({ connected, latest, history }), [connected, history, latest]);
}

export function TelemetryProvider({ children }: PropsWithChildren) {
  const value = useTelemetryConnection();
  return createElement(TelemetryContext.Provider, { value }, children);
}

export function useTelemetry(): TelemetrySocketState {
  return useContext(TelemetryContext) ?? EMPTY_STATE;
}

export const useTelemetrySocket = useTelemetry;
