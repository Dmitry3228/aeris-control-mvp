export type AlarmStatus = "ACTIVE" | "ACKNOWLEDGED" | "CLOSED";
export type CommandKind = "START" | "STOP" | "SETPOINT";
export type CommandStatus = "PENDING" | "ACKNOWLEDGED" | "REJECTED" | "TIMED_OUT";
export type OperationalStatus = "RUNNING" | "OFFLINE" | "STALE" | "NO_DATA";

export interface DeviceState {
  deviceId: string;
  timestamp: string;
  temperature: number;
  pressure: number;
  power: number;
  connected: boolean;
}

export interface TelemetryPoint {
  timestamp: string;
  temperature: number;
  pressure: number;
  power: number;
  connected: boolean;
}

export interface EquipmentSummary {
  id: string;
  name: string;
  equipmentType: string;
  location: string | null;
  enabled: boolean;
  operationalStatus: OperationalStatus;
  state: DeviceState | null;
}

export interface EquipmentDetail extends EquipmentSummary {
  history: TelemetryPoint[];
}

export interface Alarm {
  id: string;
  deviceId: string;
  ruleKey: string;
  status: AlarmStatus;
  severity: string;
  message: string;
  openedAt: string;
  updatedAt: string;
  acknowledgedAt: string | null;
  closedAt: string | null;
}

export interface Command {
  id: string;
  deviceId: string;
  kind: CommandKind;
  value: number | null;
  status: CommandStatus;
  createdAt: string;
  timeoutAt: string;
  acknowledgedAt: string | null;
}

export interface CommandRequest {
  deviceId: string;
  kind: CommandKind;
  value?: number;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  dependencies: Record<string, "ok" | "unavailable">;
}

export interface TelemetryPayload {
  timestamp?: string;
  temperature?: number;
  pressure?: number;
  power?: number;
  connected?: boolean;
}

export interface TelemetryEnvelope {
  type: "telemetry";
  epoch: string;
  sequence: number;
  deviceId: string;
  payload: TelemetryPayload;
}

export interface SessionEnvelope {
  type: "session";
  epoch: string;
  sequence: number;
}

export interface AlarmEnvelope {
  type: "alarm";
  epoch: string;
  sequence: number;
  deviceId: string;
  payload: Alarm & { action: "OPEN" | "UPDATE" | "CLOSE" };
}

export interface CommandEnvelope {
  type: "command";
  epoch: string;
  sequence: number;
  deviceId: string;
  payload: Command;
}

export type StreamEnvelope = SessionEnvelope | TelemetryEnvelope | AlarmEnvelope | CommandEnvelope;

export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
}
