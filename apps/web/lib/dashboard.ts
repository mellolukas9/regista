"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Job, Period } from "@/lib/jobs";
import { POLL_MS } from "@/lib/machines";

export type DashboardData = {
  all_clients: boolean;
  period: Period;
  generated_at: string;
  clients_active: number | null;
  runs_total: number;
  runs_running: number;
  runs_pending: number;
  runs_finished: number;
  success_rate: number | null;
  machines_total: number;
  machines_online: number;
  machines_no_signal: number;
  daily: { day: string; completed: number; failed: number }[];
  attention: {
    kind: "machine_offline" | "job_pending";
    id: string;
    label: string;
    since: string | null;
    client_name: string;
  }[];
  by_client: {
    client_id: string;
    client_name: string;
    runs: number;
    success_rate: number | null;
    machines_total: number;
    machines_online: number;
    machines_no_signal: number;
    last_run_at: string | null;
  }[];
  latest_runs: Job[];
  machines: {
    id: string;
    name: string;
    status: "pending" | "online" | "offline" | "revoked";
    last_seen_at: string | null;
    current_job: { id: string; short_code: string; status: string } | null;
  }[];
  pending_runs: Job[];
  silent: { id: string; name: string; last_seen_at: string | null; other_online: string | null }[];
};

export const dashboardKey = ["dashboard"] as const;

export function useDashboard(period: Period, contextKey: string) {
  return useQuery({
    queryKey: [...dashboardKey, period, contextKey],
    queryFn: () => api.get<DashboardData>(`/dashboard?period=${period}`),
    refetchInterval: POLL_MS,
  });
}

export function periodLabel(period: Period): string {
  return period === "today" ? "hoje" : period === "7d" ? "7 dias" : "30 dias";
}

export function percent(rate: number | null): string {
  return rate === null ? "—" : `${Math.round(rate * 100)}%`;
}
