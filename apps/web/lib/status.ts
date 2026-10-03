// Status, rótulos, tons e forma do ponto (design-system.md §3). Só máquinas por enquanto: as
// outras entidades entram com os marcos que criam esses dados.

export type Tone = "neutral" | "success" | "warning" | "danger" | "running";
export type Dot = "solid" | "hollow" | "pulse";
export type StatusKind = "machine";

export type MachineStatus = "pending" | "online" | "offline" | "revoked";

type Entry = { label: string; tone: Tone; dot: Dot };

const MACHINE: Record<MachineStatus, Entry> = {
  pending: { label: "Aguardando cadastro", tone: "warning", dot: "hollow" },
  online: { label: "Online", tone: "success", dot: "solid" },
  offline: { label: "Sem sinal", tone: "danger", dot: "solid" },
  revoked: { label: "Revogada", tone: "neutral", dot: "solid" },
};

const TABLES = { machine: MACHINE } as const;

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
