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
  /** Por que o agente recusou o pacote (lista fechada; só com `package_invalid`). */
  error_reason: string | null;
  /** A versão com que a execução rodou (null em desenvolvimento). */
  bot_version: string | null;
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

/** Segunda linha do banner de `package_invalid` (design-system.md §15): um texto fixo por motivo. */
const PACKAGE_REASON_TEXT: Record<string, string> = {
  hash_mismatch: "O arquivo do pacote não bate com o hash publicado.",
  signature_invalid: "A assinatura do pacote não confere.",
  unknown_key: "O pacote foi assinado com uma chave em que esta máquina não confia.",
  wrong_client: "O pacote é de outro cliente.",
  wrong_package: "O pacote não é deste robô.",
  wrong_version: "A versão do pacote não é a desta execução.",
  unsafe_archive: "O pacote tem um arquivo com caminho inseguro.",
  too_large: "O pacote tem arquivos demais ou grandes demais.",
  malformed_package: "O pacote está corrompido ou fora do formato esperado.",
};

export function packageReasonText(reason: string | null): string | null {
  return reason ? (PACKAGE_REASON_TEXT[reason] ?? null) : null;
}

/** O que cada recusa do agente mostra (§15). Nada daqui vem de texto livre do agente. */
function refusalText(job: Pick<Job, "error_code" | "bot_version"> & { package_name?: string }): string | null {
  switch (job.error_code) {
    case "package_invalid":
      return "A máquina recusou o pacote do robô. Avise a equipe Artemisys.";
    case "robot_not_allowed":
      return `Este robô não está na lista de robôs permitidos desta máquina. Para liberar, rode regista-agent allow ${job.package_name ?? "<pacote>"} como administrador na própria máquina.`;
    case "runtime_missing":
      return `Esta máquina ainda não está preparada para a versão ${job.bot_version ?? ""} do robô. Peça a quem cuida da máquina para rodar regista-agent setup como administrador.`;
    case "environment_failed":
      return "Não foi possível montar o ambiente do robô nesta máquina. Veja os logs da execução e avise a equipe Artemisys.";
    case "robot_host_unavailable":
      return "O serviço que roda os robôs nesta máquina não respondeu. Peça a quem cuida da máquina para rodar regista-agent diagnose como administrador.";
    default:
      return null;
  }
}

export function errorText(
  code: string | null,
  job?: Pick<Job, "bot_version"> & { package_name?: string },
): string | null {
  if (!code) return null;
  const refusal = refusalText({ error_code: code, bot_version: job?.bot_version ?? null, package_name: job?.package_name });
  return refusal ?? ERROR_TEXT[code] ?? "A execução terminou com erro.";
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
