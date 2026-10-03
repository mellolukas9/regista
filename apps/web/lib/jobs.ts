"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { formatListDate } from "@/lib/format";
import { POLL_MS } from "@/lib/machines";
import type { JobStatus } from "@/lib/status";

// "Execução" na interface, `job` no código (docs/specs/frontend.md).

export type Job = {
  id: string;
  short_code: string;
  bot_id: string;
  bot_name: string;
  status: JobStatus;
  trigger: string;
  triggered_by: string | null;
  machine_id: string | null;
  machine_name: string | null;
  pool_id: string;
  pool_name: string;
  client_id: string;
  client_name: string;
  created_at: string;
  assigned_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested_at: string | null;
  error_code: string | null;
  error_message: string | null;
  items_successful: number;
  items_failed: number;
  items_abandoned: number;
  items_total: number;
};

export type JobDetail = Job & {
  package_name: string;
  params: Record<string, unknown>;
  pool_has_online_machine: boolean;
};

export type JobPage = { items: Job[]; total: number; page: number; per_page: number };

export type LogLine = {
  seq: number;
  ts: string;
  level: "INFO" | "WARN" | "ERROR";
  message: string;
  item_ref: string | null;
  attempt: number | null;
};

export type LogPage = {
  items: LogLine[];
  level_counts: Record<"INFO" | "WARN" | "ERROR", number>;
  has_more: boolean;
};

export type Artifact = {
  id: string;
  kind: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
  uploaded_at: string;
};

export type Period = "today" | "7d" | "30d";
export const PERIODS: { value: Period; label: string }[] = [
  { value: "today", label: "Hoje" },
  { value: "7d", label: "7 dias" },
  { value: "30d", label: "30 dias" },
];
export const DEFAULT_PERIOD: Period = "30d";

export const jobsKey = ["jobs"] as const;
export const ACTIVE: JobStatus[] = ["pending", "assigned", "running"];

export function isActive(status: JobStatus): boolean {
  return ACTIVE.includes(status);
}

/** "4min 12s", "42s", "1 h 05min": do início até o fim (ou até agora, se ainda roda). */
export function duration(job: Pick<Job, "started_at" | "finished_at" | "status">, now = Date.now()): string {
  if (!job.started_at) return "—";
  const end = job.finished_at ? new Date(job.finished_at).getTime() : now;
  const seconds = Math.max(0, Math.round((end - new Date(job.started_at).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}min ${String(seconds % 60).padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${String(minutes % 60).padStart(2, "0")}min`;
}

/** Quando a execução "começou" para quem olha a lista: o início real ou, se ainda não rodou, a criação. */
export function startedLabel(job: Pick<Job, "started_at" | "created_at">): string {
  return formatListDate(job.started_at ?? job.created_at);
}

export function triggerLabel(job: Pick<Job, "trigger" | "triggered_by">): string {
  if (job.trigger === "schedule") return "Agendamento";
  if (job.trigger === "api") return "API";
  return "Manual";
}

/** Texto de cada `error_code` (design-system.md §14). O servidor só devolve o código. */
const ERROR_TEXT: Record<string, string> = {
  machine_lost: "A máquina parou de responder durante a execução.",
  machine_revoked: "A máquina foi revogada durante a execução.",
  timeout: "O robô passou do tempo máximo.",
  robot_failed: "O robô terminou com erro.",
  robot_not_found: "O robô não foi encontrado nesta máquina.",
  internal: "O agente teve um problema durante a execução.",
};

export function errorText(code: string | null): string | null {
  if (!code) return null;
  return ERROR_TEXT[code] ?? "A execução terminou com erro.";
}

export function itemsLabel(job: Pick<Job, "items_successful" | "items_failed" | "items_abandoned">) {
  return { ok: job.items_successful, bad: job.items_failed + job.items_abandoned };
}

/** Pendentes e ativas do contexto: alimenta o contador "Execuções" da sidebar e o Dashboard. */
export function useJobsSummary() {
  return useQuery({
    queryKey: [...jobsKey, "summary"],
    refetchInterval: POLL_MS,
    queryFn: () => api.get<{ pending: number; active: number }>("/jobs/summary"),
  });
}

/** Janela em milissegundos desde a pergunta (para "há 2 h · nenhuma máquina livre"). */
export function waitingSince(job: Pick<Job, "created_at">, now = Date.now()): number {
  return now - new Date(job.created_at).getTime();
}
