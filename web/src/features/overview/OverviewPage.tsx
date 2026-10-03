import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { Alarm, Command } from "../../api/types";
import { AlarmIcon, ArrowRightIcon, EnergyIcon, EquipmentIcon, FilterIcon, SearchIcon } from "../../components/Icons";
import { useTelemetrySocket } from "../../hooks/useTelemetrySocket";
import { AlarmCard } from "../alarms/AlarmCard";
import { useAcknowledgeAlarm } from "../alarms/AlarmsPage";
import { EquipmentTable } from "../equipment/EquipmentTable";
import { PressureTrend } from "./PressureTrend";

export function operationalAlarms(alarms: Alarm[]) {
  return alarms.filter((alarm) => alarm.status !== "CLOSED");
}

function MetricCard({ className, icon, value, label }: { className: string; icon: React.ReactNode; value: string; label: string }) {
  return (
    <article className={`metric-card ${className}`}>
      {icon}
      <div><strong>{value}</strong><span>{label}</span></div>
    </article>
  );
}

function formatCommand(command: Command) {
  if (command.kind === "SETPOINT") return `Set ${command.deviceId} setpoint to ${command.value} °C`;
  return `${command.kind === "START" ? "Start" : "Stop"} ${command.deviceId}`;
}

function CommandActivity({ commands }: { commands: Command[] }) {
  return (
    <section className="panel activity-panel">
      <header className="panel-heading"><h2>Command activity</h2><Link to="/equipment">View all <ArrowRightIcon size={16} /></Link></header>
      <div className="activity-list">
        {commands.slice(0, 4).map((command) => (
          <div className="activity-row" key={command.id}>
            <span className={`timeline-dot ${command.status === "ACKNOWLEDGED" ? "is-success" : ""}`} />
            <time dateTime={command.createdAt}>{new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(command.createdAt))}</time>
            <div>
              <strong>{command.status === "ACKNOWLEDGED" ? "Command acknowledged" : "Command sent"}</strong>
              <span>{formatCommand(command)}</span>
            </div>
          </div>
        ))}
        {commands.length === 0 ? <p className="empty-state">No command activity</p> : null}
      </div>
    </section>
  );
}

export function OverviewPage() {
  const equipment = useQuery({ queryKey: ["equipment"], queryFn: api.equipment });
  const alarms = useQuery({ queryKey: ["alarms"], queryFn: api.alarms });
  const commands = useQuery({ queryKey: ["commands"], queryFn: () => api.commands() });
  const telemetry = useTelemetrySocket();
  const acknowledge = useAcknowledgeAlarm();
  const [search, setSearch] = useState("");
  const [zone, setZone] = useState("ALL");
  const allEquipment = useMemo(
    () => (equipment.data ?? []).map((item) => {
      const live = telemetry.latest[item.id]?.point;
      if (!live) return item;
      return {
        ...item,
        operationalStatus: live.connected ? "RUNNING" as const : "OFFLINE" as const,
        state: {
          deviceId: item.id,
          timestamp: live.timestamp,
          temperature: Number.isFinite(live.temperature) ? live.temperature : item.state?.temperature ?? Number.NaN,
          pressure: Number.isFinite(live.pressure) ? live.pressure : item.state?.pressure ?? Number.NaN,
          power: Number.isFinite(live.power) ? live.power : item.state?.power ?? Number.NaN,
          connected: live.connected,
        },
      };
    }),
    [equipment.data, telemetry.latest],
  );
  const zones = useMemo(() => Array.from(new Set(allEquipment.map((item) => item.location).filter(Boolean) as string[])).sort(), [allEquipment]);
  const visibleEquipment = useMemo(() => {
    const term = search.trim().toLocaleLowerCase();
    return allEquipment.filter((item) => {
      const matchesZone = zone === "ALL" || item.location === zone;
      return matchesZone && `${item.id} ${item.location ?? ""}`.toLocaleLowerCase().includes(term);
    });
  }, [allEquipment, search, zone]);
  const unresolvedAlarms = operationalAlarms(alarms.data ?? []);
  const focusEquipment = allEquipment.find((item) => item.id === unresolvedAlarms[0]?.deviceId) ?? [...allEquipment].sort((left, right) => (right.state?.pressure ?? 0) - (left.state?.pressure ?? 0))[0];
  const online = allEquipment.filter((item) => item.operationalStatus === "RUNNING").length;

  return (
    <section className="overview-page">
      <header className="overview-title">
        <div><h1>Live operations</h1><p>Realtime monitoring and control of your HVAC fleet</p></div>
        <div className="metrics" aria-label="Fleet metrics">
          <MetricCard className="is-success" icon={<EquipmentIcon />} value={String(online)} label="units online" />
          <MetricCard className="is-warning" icon={<AlarmIcon />} value={String(unresolvedAlarms.length)} label="open alarms" />
          <MetricCard className="is-accent" icon={<EnergyIcon />} value="—" label="MWh today" />
        </div>
      </header>

      {unresolvedAlarms[0] ? (
        <div className="mobile-primary-alarm">
          <AlarmCard
            acknowledgementError={acknowledge.isError && acknowledge.variables === unresolvedAlarms[0].id ? acknowledge.error.message : undefined}
            acknowledgementPending={acknowledge.isPending}
            alarm={unresolvedAlarms[0]}
            onAcknowledge={(alarm) => acknowledge.mutate(alarm.id)}
          />
        </div>
      ) : null}

      <div className="overview-grid">
        <div className="overview-main">
          <section className="panel fleet-panel">
            <header className="panel-heading fleet-heading">
              <h2>Equipment fleet</h2>
              <div className="fleet-controls">
                <label className="search-control">
                  <span className="sr-only">Search equipment</span><SearchIcon size={17} />
                  <input aria-label="Search equipment" onChange={(event) => setSearch(event.target.value)} placeholder="Search units, zones..." type="search" value={search} />
                </label>
                <label className="select-control">
                  <span className="sr-only">Zone</span><FilterIcon size={17} />
                  <select aria-label="Zone" onChange={(event) => setZone(event.target.value)} value={zone}>
                    <option value="ALL">All zones</option>
                    {zones.map((item) => <option key={item} value={item}>{item}</option>)}
                  </select>
                </label>
              </div>
            </header>
            {equipment.isLoading ? <p className="loading-state">Loading…</p> : null}
            {equipment.error ? <p className="error-state">{equipment.error.message}</p> : null}
            {equipment.data ? <EquipmentTable compact equipment={visibleEquipment} /> : null}
          </section>
          <PressureTrend equipment={focusEquipment} key={focusEquipment?.id ?? "equipment"} liveHistory={telemetry.history[focusEquipment?.id ?? ""] ?? []} />
          <div className="mobile-activity"><CommandActivity commands={commands.data ?? []} /></div>
        </div>

        <aside className="side-rail">
          <section className="panel alarms-panel">
            <header className="panel-heading"><h2>Open alarms ({unresolvedAlarms.length})</h2><Link to="/alarms">View all <ArrowRightIcon size={16} /></Link></header>
            <div className="alarm-stack">
              {unresolvedAlarms.slice(0, 3).map((alarm: Alarm) => (
                <AlarmCard
                  acknowledgementError={acknowledge.isError && acknowledge.variables === alarm.id ? acknowledge.error.message : undefined}
                  acknowledgementPending={acknowledge.isPending && acknowledge.variables === alarm.id}
                  alarm={alarm}
                  key={alarm.id}
                  onAcknowledge={(selected) => acknowledge.mutate(selected.id)}
                />
              ))}
            </div>
          </section>
          <CommandActivity commands={commands.data ?? []} />
        </aside>
      </div>
    </section>
  );
}
