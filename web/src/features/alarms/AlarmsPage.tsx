import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { Alarm } from "../../api/types";
import { AlarmCard } from "./AlarmCard";

export function useAcknowledgeAlarm() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (alarmId: string) => api.acknowledgeAlarm(alarmId),
    onSuccess: (updated) => {
      queryClient.setQueryData<Alarm[]>(["alarms"], (current) =>
        current?.map((alarm) => (alarm.id === updated.id ? updated : alarm)),
      );
      void queryClient.invalidateQueries({ queryKey: ["alarms"] });
    },
  });
}

export function AlarmsPage() {
  const alarms = useQuery({ queryKey: ["alarms"], queryFn: api.alarms });
  const acknowledge = useAcknowledgeAlarm();

  return (
    <section className="route-page">
      <header className="route-heading">
        <h1>Alarm center</h1>
      </header>
      {alarms.isLoading ? <p className="loading-state">Loading…</p> : null}
      {alarms.error ? <p className="error-state">{alarms.error.message}</p> : null}
      <div className="alarms-list">
        {alarms.data?.map((alarm) => (
          <AlarmCard
            alarm={alarm}
            acknowledgementError={acknowledge.isError && acknowledge.variables === alarm.id ? acknowledge.error.message : undefined}
            acknowledgementPending={acknowledge.isPending && acknowledge.variables === alarm.id}
            key={alarm.id}
            onAcknowledge={(selected) => acknowledge.mutate(selected.id)}
          />
        ))}
      </div>
      {alarms.data?.length === 0 ? <p className="empty-state">No alarms found</p> : null}
    </section>
  );
}
