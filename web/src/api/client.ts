import type {
  Alarm,
  Command,
  CommandRequest,
  EquipmentDetail,
  EquipmentSummary,
  HealthResponse,
  ProblemDetail,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`;
    try {
      const problem = (await response.json()) as ProblemDetail;
      detail = problem.detail || problem.title || detail;
    } catch {
      // The status-derived message remains useful when a proxy returns non-JSON.
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),
  equipment: () => request<EquipmentSummary[]>("/api/equipment"),
  equipmentDetail: (deviceId: string, start?: string, end?: string) => {
    const params = start && end ? `?${new URLSearchParams({ start, end })}` : "";
    return request<EquipmentDetail>(`/api/equipment/${encodeURIComponent(deviceId)}${params}`);
  },
  alarms: () => request<Alarm[]>("/api/alarms"),
  acknowledgeAlarm: (alarmId: string) =>
    request<Alarm>(`/api/alarms/${encodeURIComponent(alarmId)}/ack`, { method: "POST" }),
  commands: (deviceId?: string) => {
    const params = deviceId ? `?${new URLSearchParams({ deviceId })}` : "";
    return request<Command[]>(`/api/commands${params}`);
  },
  createCommand: (command: CommandRequest) =>
    request<Command>("/api/commands", {
      method: "POST",
      body: JSON.stringify(command),
    }),
};
