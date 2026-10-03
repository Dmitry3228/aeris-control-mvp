import type { Alarm } from "../../api/types";
import { operationalAlarms } from "./OverviewPage";

const alarm = (status: Alarm["status"]): Alarm => ({
  id: status,
  deviceId: "AHU-204",
  ruleKey: "pressure_high",
  status,
  severity: "WARNING",
  message: "High pressure",
  openedAt: "2026-10-03T08:00:00Z",
  updatedAt: "2026-10-03T08:00:00Z",
  acknowledgedAt: status === "ACKNOWLEDGED" ? "2026-10-03T08:00:01Z" : null,
  closedAt: status === "CLOSED" ? "2026-10-03T08:00:02Z" : null,
});

test("operational alarm summary keeps acknowledged unresolved alarms", () => {
  expect(operationalAlarms([
    alarm("ACTIVE"),
    alarm("ACKNOWLEDGED"),
    alarm("CLOSED"),
  ]).map((item) => item.status)).toEqual(["ACTIVE", "ACKNOWLEDGED"]);
});
