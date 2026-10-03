import { Link } from "react-router-dom";

import type { Alarm } from "../../api/types";
import { AlarmIcon, ChevronRightIcon } from "../../components/Icons";

function time(value: string) {
  return new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
}

interface AlarmCardProps {
  alarm: Alarm;
  acknowledgementError?: string;
  acknowledgementPending?: boolean;
  onAcknowledge: (alarm: Alarm) => void;
}

export function AlarmCard({ alarm, acknowledgementError, acknowledgementPending = false, onAcknowledge }: AlarmCardProps) {
  const statusLabel = alarm.status === "ACTIVE"
    ? "Active"
    : alarm.status === "ACKNOWLEDGED"
      ? "Acknowledged"
      : "Closed";
  return (
    <article
      className={`alarm-card is-${alarm.status.toLocaleLowerCase()}`}
      data-alarm-id={alarm.id}
      data-alarm-status={alarm.status}
    >
      <AlarmIcon className="alarm-card-icon" size={30} />
      <div className="alarm-card-copy">
        <div className="alarm-card-heading">
          <strong>{alarm.deviceId}</strong>
          <div className="alarm-card-meta">
            <span className={`alarm-status-label is-${alarm.status.toLocaleLowerCase()}`}>{statusLabel}</span>
            <time dateTime={alarm.updatedAt}>{time(alarm.updatedAt)}</time>
          </div>
        </div>
        <p>{alarm.message}</p>
        <small aria-label="Alarm measurement unavailable">—</small>
        <div className="alarm-actions">
          {alarm.status === "ACTIVE" ? (
            <button
              className="button button-primary"
              disabled={acknowledgementPending}
              onClick={() => onAcknowledge(alarm)}
              type="button"
              aria-label={`Acknowledge ${alarm.deviceId} alarm`}
            >
              Acknowledge
            </button>
          ) : null}
          <Link className="button button-secondary" to={`/equipment/${alarm.deviceId}`}>View</Link>
        </div>
        {acknowledgementError ? <p className="alarm-error" role="alert">{acknowledgementError}</p> : null}
      </div>
      <ChevronRightIcon className="alarm-mobile-chevron" size={22} />
    </article>
  );
}
