import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../../api/client";
import type { Command, CommandKind, OperationalStatus } from "../../api/types";
import { ChevronRightIcon } from "../../components/Icons";
import { useTelemetry } from "../../hooks/useTelemetrySocket";
import { PressureTrend } from "../overview/PressureTrend";

function commandStatus(status: Command["status"]) {
  return status.toLocaleLowerCase().replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

function statusLabel(status: OperationalStatus) {
  if (status === "RUNNING") return "Running";
  if (status === "OFFLINE") return "Offline";
  if (status === "STALE") return "Stale";
  return "No data";
}

export function EquipmentDetailPage() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const telemetry = useTelemetry();
  const [kind, setKind] = useState<CommandKind>("START");
  const [value, setValue] = useState("21");
  const end = new Date();
  const start = new Date(end.getTime() - 60 * 60 * 1_000);
  const detail = useQuery({
    queryKey: ["equipment", id],
    queryFn: () => api.equipmentDetail(id, start.toISOString(), end.toISOString()),
    enabled: Boolean(id),
  });
  const commands = useQuery({
    queryKey: ["commands", id],
    queryFn: () => api.commands(id),
    enabled: Boolean(id),
    refetchInterval: (query) => query.state.data?.some((command) => command.status === "PENDING") ? 1_000 : false,
  });
  const createCommand = useMutation({
    mutationFn: () => api.createCommand({
      deviceId: id,
      kind,
      ...(kind === "SETPOINT" ? { value: Number(value) } : {}),
    }),
    onSuccess: (created) => {
      queryClient.setQueryData<Command[]>(["commands", id], (current) => [created, ...(current ?? [])]);
      void queryClient.invalidateQueries({ queryKey: ["commands", id] });
    },
  });

  if (detail.isLoading) return <p className="loading-state">Loading…</p>;
  if (detail.error) return <p className="error-state">{detail.error.message}</p>;
  if (!detail.data) return null;

  const state = detail.data.state;
  return (
    <section className="route-page detail-page">
      <Link className="back-link" to="/equipment">Equipment <ChevronRightIcon size={16} /></Link>
      <header className="route-heading detail-heading">
        <div>
          <h1>{detail.data.id}</h1>
          <p>{detail.data.name}</p>
        </div>
        <span className={`connection-label is-${detail.data.operationalStatus.toLocaleLowerCase().replace("_", "-")}`}>
          <span className="status-dot" />{statusLabel(detail.data.operationalStatus)}
        </span>
      </header>

      <div className="detail-grid">
        <section className="panel detail-telemetry">
          <h2>Equipment details</h2>
          <dl className="telemetry-values">
            <div><dt>Pressure</dt><dd>{state ? `${state.pressure.toFixed(1)} bar` : "—"}</dd></div>
            <div><dt>Temperature</dt><dd>{state ? `${state.temperature.toFixed(1)} °C` : "—"}</dd></div>
            <div><dt>Power</dt><dd>{state ? `${state.power.toFixed(1)} kW` : "—"}</dd></div>
            <div><dt>Zone</dt><dd>{detail.data.location ?? "—"}</dd></div>
          </dl>
        </section>

        <section className="panel command-panel">
          <h2>Command</h2>
          <form onSubmit={(event) => { event.preventDefault(); createCommand.mutate(); }}>
            <label>
              <span>Command</span>
              <select value={kind} onChange={(event) => setKind(event.target.value as CommandKind)}>
                <option value="START">Start</option>
                <option value="STOP">Stop</option>
                <option value="SETPOINT">Setpoint</option>
              </select>
            </label>
            {kind === "SETPOINT" ? (
              <label>
                <span>Value</span>
                <input min="0" required step="0.1" type="number" value={value} onChange={(event) => setValue(event.target.value)} />
              </label>
            ) : null}
            <button className="button button-primary" disabled={createCommand.isPending} type="submit">Send command</button>
          </form>
          {createCommand.error ? <p className="error-state">{createCommand.error.message}</p> : null}
          <div className="command-list" aria-live="polite">
            {commands.data?.map((command) => (
              <div className="command-row" data-command-id={command.id} key={command.id}>
                <span>{command.kind === "SETPOINT" ? `${command.kind} ${command.value}` : command.kind}</span>
                <strong className={`command-status is-${command.status.toLocaleLowerCase()}`}>{commandStatus(command.status)}</strong>
              </div>
            ))}
          </div>
        </section>
        <div className="detail-trend">
          <PressureTrend
            equipment={detail.data}
            liveHistory={telemetry.history[id] ?? detail.data.history}
          />
        </div>
      </div>
    </section>
  );
}
