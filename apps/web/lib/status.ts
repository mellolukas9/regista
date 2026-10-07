// Status, rótulos, tons e forma do ponto (design-system.md §3). Máquinas e execuções por enquanto: as
// outras entidades entram com os marcos que criam esses dados.

export type Tone = "neutral" | "success" | "warning" | "danger" | "running";
export type Dot = "solid" | "hollow" | "pulse";
export type StatusKind = "machine" | "job";

export type MachineStatus = "pending" | "online" | "offline" | "revoked";
export type JobStatus = "pending" | "assigned" | "running" | "completed" | "failed" | "cancelled";

type Entry = { label: string; tone: Tone; dot: Dot };

const MACHINE: Record<MachineStatus, Entry> = {
  pending: { label: "Aguardando cadastro", tone: "warning", dot: "hollow" },
  online: { label: "Online", tone: "success", dot: "solid" },
  offline: { label: "Sem sinal", tone: "danger", dot: "solid" },
  revoked: { label: "Revogada", tone: "neutral", dot: "solid" },
};

const JOB: Record<JobStatus, Entry> = {
  pending: { label: "Pendente", tone: "warning", dot: "hollow" },
  assigned: { label: "Atribuído", tone: "running", dot: "hollow" },
  running: { label: "Executando", tone: "running", dot: "pulse" },
  completed: { label: "Concluído", tone: "success", dot: "solid" },
  failed: { label: "Falhou", tone: "danger", dot: "solid" },
  cancelled: { label: "Cancelado", tone: "neutral", dot: "solid" },
};

const TABLES = { machine: MACHINE, job: JOB } as const;

export function statusEntry(kind: StatusKind, status: string): Entry {
  const table: Record<string, Entry> = TABLES[kind];
  return table[status] ?? { label: status, tone: "neutral", dot: "solid" };
}

export const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-neutral",
  success: "text-success",
  warning: "text-warning-text",
  danger: "text-danger-text",
  running: "text-running",
};

export const TONE_DOT: Record<Tone, string> = {
  neutral: "bg-neutral border-neutral",
  success: "bg-success border-success",
  warning: "bg-warning border-warning",
  danger: "bg-danger border-danger",
  running: "bg-running border-running",
};
