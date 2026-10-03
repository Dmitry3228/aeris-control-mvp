import { useQuery } from "@tanstack/react-query";
import { useDeferredValue, useMemo, useState } from "react";

import { api } from "../../api/client";
import { FilterIcon, SearchIcon } from "../../components/Icons";
import { EquipmentTable } from "./EquipmentTable";

export function EquipmentPage() {
  const equipment = useQuery({ queryKey: ["equipment"], queryFn: api.equipment });
  const [search, setSearch] = useState("");
  const [equipmentType, setEquipmentType] = useState("ALL");
  const deferredSearch = useDeferredValue(search.trim().toLocaleLowerCase());
  const types = useMemo(
    () => Array.from(new Set(equipment.data?.map((item) => item.equipmentType) ?? [])).sort(),
    [equipment.data],
  );
  const filtered = useMemo(
    () =>
      (equipment.data ?? []).filter((item) => {
        const matchesType = equipmentType === "ALL" || item.equipmentType === equipmentType;
        const searchable = `${item.id} ${item.name} ${item.location ?? ""}`.toLocaleLowerCase();
        return matchesType && searchable.includes(deferredSearch);
      }),
    [deferredSearch, equipment.data, equipmentType],
  );

  return (
    <section className="route-page">
      <header className="route-heading">
        <h1>Equipment</h1>
      </header>
      <div className="filter-bar">
        <label className="search-control">
          <span className="sr-only">Search equipment</span>
          <SearchIcon size={18} />
          <input
            aria-label="Search equipment"
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search units, zones..."
            type="search"
            value={search}
          />
        </label>
        <label className="select-control">
          <span className="sr-only">Equipment type</span>
          <FilterIcon size={18} />
          <select aria-label="Equipment type" onChange={(event) => setEquipmentType(event.target.value)} value={equipmentType}>
            <option value="ALL">All types</option>
            {types.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
        </label>
      </div>
      {equipment.isLoading ? <p className="loading-state">Loading…</p> : null}
      {equipment.error ? <p className="error-state">{equipment.error.message}</p> : null}
      {equipment.data ? <EquipmentTable equipment={filtered} /> : null}
    </section>
  );
}
