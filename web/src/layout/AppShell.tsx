import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { api } from "../api/client";
import { AlarmIcon, BuildingIcon, ChevronDownIcon, EquipmentIcon, HomeIcon, MenuIcon, UserIcon } from "../components/Icons";

const navigation = [
  { to: "/", label: "Overview", icon: HomeIcon, end: true },
  { to: "/equipment", label: "Equipment", icon: EquipmentIcon, end: false },
  { to: "/alarms", label: "Alarm center", icon: AlarmIcon, end: false },
];

function Brand() {
  return <span className="brand"><strong>AERIS</strong> CONTROL</span>;
}

function Navigation({ mobile = false, onNavigate }: { mobile?: boolean; onNavigate?: () => void }) {
  return (
    <nav aria-label={mobile ? "Mobile navigation" : "Primary navigation"} className={mobile ? "mobile-navigation" : "rail-navigation"}>
      {navigation.map(({ to, label, icon: Icon, end }) => (
        <NavLink end={end} key={to} onClick={onNavigate} to={to}>
          <Icon size={mobile ? 28 : 24} /><span>{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

function formatDate(date: Date) {
  return new Intl.DateTimeFormat("en", { weekday: "short", month: "short", day: "numeric", year: "numeric" }).format(date);
}

function formatTime(date: Date) {
  return new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
}

export function AppShell() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 30_000, retry: false });
  const healthy = health.data?.status === "ok";

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="app-shell">
      <aside className="desktop-rail">
        <div className="rail-brand"><Brand /></div>
        <Navigation />
        <footer><p>Building better<br />air for a healthier<br />tomorrow.</p><span>v1.2.0</span></footer>
      </aside>

      <div className="app-frame">
        <header className="desktop-topbar">
          <div className="plant-label"><BuildingIcon size={22} /><span>Plant 01 – Riverside</span><ChevronDownIcon size={16} /></div>
          <div className="topbar-right">
            <span className={`system-status ${healthy ? "" : "is-degraded"}`}><span className="status-dot" />{healthy ? "System healthy" : "System degraded"}</span>
            <time dateTime={now.toISOString()}>{formatDate(now)} <span>{formatTime(now)}</span></time>
            <span className="operator-label"><UserIcon size={21} />Operator <ChevronDownIcon size={16} /></span>
          </div>
        </header>

        <header className="mobile-header">
          <Brand />
          <div className="mobile-health">
            <span className={`system-status ${healthy ? "" : "is-degraded"}`}><span className="status-dot" />{healthy ? "System healthy" : "System degraded"}</span>
            <time dateTime={now.toISOString()}>{formatDate(now)}&nbsp; {formatTime(now).slice(0, 5)}</time>
          </div>
          <button aria-expanded={menuOpen} aria-label="Menu" className="menu-button" onClick={() => setMenuOpen((open) => !open)} type="button"><MenuIcon size={28} /></button>
          {menuOpen ? <div className="mobile-menu"><Navigation mobile onNavigate={() => setMenuOpen(false)} /></div> : null}
        </header>

        <main><Outlet /></main>
      </div>
      <Navigation mobile />
    </div>
  );
}
