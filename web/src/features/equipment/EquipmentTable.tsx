import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { Link } from "react-router-dom";

import type { EquipmentSummary } from "../../api/types";
import { ChevronRightIcon, MoreIcon } from "../../components/Icons";

const column = createColumnHelper<EquipmentSummary>();

function number(value: number | undefined, unit: string, digits = 1) {
  return Number.isFinite(value) ? `${value?.toFixed(digits)} ${unit}` : "—";
}

const columns = [
  column.accessor("id", { header: "Unit", cell: (context) => context.getValue() }),
  column.accessor("location", { header: "Zone", cell: (context) => context.getValue() ?? "—" }),
  column.accessor((row) => row.state?.pressure, {
    id: "pressure",
    header: "Pressure",
    cell: (context) => number(context.getValue(), "bar"),
  }),
  column.accessor((row) => row.state?.temperature, {
    id: "temperature",
    header: "Temperature",
    cell: (context) => number(context.getValue(), "°C"),
  }),
  column.accessor((row) => row.state?.power, {
    id: "power",
    header: "Power",
    cell: (context) => number(context.getValue(), "kW"),
  }),
  column.accessor((row) => row.operationalStatus, {
    id: "status",
    header: "Status",
    cell: (context) => {
      const status = context.getValue();
      const highPressure = status === "RUNNING" && (context.row.original.state?.pressure ?? 0) >= 7.5;
      const label = status === "RUNNING"
        ? highPressure ? "High pressure" : "Running"
        : status === "OFFLINE" ? "Offline" : status === "STALE" ? "Stale" : "No data";
      return (
        <span className={`equipment-status is-${status.toLocaleLowerCase().replace("_", "-")} ${highPressure ? "is-warning" : ""}`}>
          <span className="status-dot" />
          {label}
        </span>
      );
    },
  }),
  column.display({
    id: "open",
    header: "",
    cell: (context) => (
      <Link className="row-link" aria-label={`View ${context.row.original.id}`} to={`/equipment/${context.row.original.id}`}>
        <ChevronRightIcon className="mobile-row-icon" size={19} />
        <MoreIcon className="desktop-row-icon" size={19} />
      </Link>
    ),
  }),
];

interface EquipmentTableProps {
  equipment: EquipmentSummary[];
  compact?: boolean;
}

export function EquipmentTable({ equipment, compact = false }: EquipmentTableProps) {
  const table = useReactTable({ data: equipment, columns, getCoreRowModel: getCoreRowModel() });

  return (
    <div className={`equipment-table-wrap ${compact ? "is-compact" : ""}`}>
      <table className="equipment-table">
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <th key={header.id}>{header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}</th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr data-testid="equipment-row" key={row.id} className={row.original.operationalStatus === "RUNNING" && (row.original.state?.pressure ?? 0) >= 7.5 ? "is-selected" : undefined}>
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {equipment.length === 0 ? <p className="empty-state">No equipment found</p> : null}
    </div>
  );
}
