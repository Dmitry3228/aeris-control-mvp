import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Icon({ size = 24, children, ...props }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      {...props}
    >
      {children}
    </svg>
  );
}

const stroke = { stroke: "currentColor", strokeLinecap: "round" as const, strokeLinejoin: "round" as const, strokeWidth: 1.8 };

export function HomeIcon(props: IconProps) {
  return <Icon {...props}><path {...stroke} d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z" /></Icon>;
}
export function EquipmentIcon(props: IconProps) {
  return <Icon {...props}><rect {...stroke} x="3" y="4" width="18" height="7" rx="1" /><rect {...stroke} x="3" y="14" width="18" height="7" rx="1" /><path {...stroke} d="M7 8h.01M7 18h.01M17 8h1M17 18h1" /></Icon>;
}
export function AlarmIcon(props: IconProps) {
  return <Icon {...props}><path {...stroke} d="M10.3 3.5 2.8 18a2 2 0 0 0 1.8 3h14.8a2 2 0 0 0 1.8-3L13.7 3.5a2 2 0 0 0-3.4 0Z" /><path {...stroke} d="M12 9v4M12 17h.01" /></Icon>;
}
export function BuildingIcon(props: IconProps) {
  return <Icon {...props}><path {...stroke} d="M4 21V7h6V3h5v6h5v12M2 21h20M7 10h1M7 14h1M7 18h1M12 6h1M12 10h1M12 14h1M12 18h1M17 12h1M17 16h1" /></Icon>;
}
export function EnergyIcon(props: IconProps) {
  return <Icon {...props}><path {...stroke} d="m13 2-9 12h7l-1 8 9-12h-7Z" /></Icon>;
}
export function UserIcon(props: IconProps) {
  return <Icon {...props}><circle {...stroke} cx="12" cy="8" r="4" /><path {...stroke} d="M4 21a8 8 0 0 1 16 0" /></Icon>;
}
export function SearchIcon(props: IconProps) {
  return <Icon {...props}><circle {...stroke} cx="11" cy="11" r="7" /><path {...stroke} d="m20 20-4-4" /></Icon>;
}
export function ChevronRightIcon(props: IconProps) {
  return <Icon {...props}><path {...stroke} d="m9 18 6-6-6-6" /></Icon>;
}
export function ChevronDownIcon(props: IconProps) {
  return <Icon {...props}><path {...stroke} d="m6 9 6 6 6-6" /></Icon>;
}
export function ArrowRightIcon(props: IconProps) {
  return <Icon {...props}><path {...stroke} d="M5 12h14M13 6l6 6-6 6" /></Icon>;
}
export function MenuIcon(props: IconProps) {
  return <Icon {...props}><path {...stroke} d="M4 6h16M4 12h16M4 18h16" /></Icon>;
}
export function FilterIcon(props: IconProps) {
  return <Icon {...props}><path {...stroke} d="M4 6h16M7 12h10M10 18h4" /></Icon>;
}
export function MoreIcon(props: IconProps) {
  return <Icon {...props}><circle cx="5" cy="12" r="1.2" fill="currentColor" /><circle cx="12" cy="12" r="1.2" fill="currentColor" /><circle cx="19" cy="12" r="1.2" fill="currentColor" /></Icon>;
}
