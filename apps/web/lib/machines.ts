"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { formatDate, formatListDate, relativeTime } from "@/lib/format";
import type { MachineStatus } from "@/lib/status";

export type MachineMode = "service" | "session" | "oneshot";

export type Machine = {
  id: string;
  name: string;
  status: MachineStatus;
  mode: MachineMode;
  pool_id: string;
  pool_name: string;
  client_id: string;
  client_name: string;
  last_seen_at: string | null;
  agent_version: string | null;
  key_expires_at: string | null;
  key_created_at: string | null;
  created_at: string;
  /** A execução em andamento nesta máquina (coluna "Agora"). */
  current_job: { id: string; short_code: string; status: string } | null;
};

export type MachineDetail = Machine & {
  os_info: Record<string, string>;
  max_concurrency: number;
  enrolled_at: string | null;
  created_by: string | null;
  revoked_at: string | null;
};

export type Pool = {
  id: string;
  name: string;
  kind: string;
  client_id: string;
  client_name: string;
  machines_total: number;
  machines_online: number;
  created_at: string;
  /** Bots ativos que rodam neste pool ("Roda: ..."). */
  bot_names: string[];
};

export type IssuedKey = {
  machine_id: string;
  name: string;
  enrollment_key: string;
  expires_at: string;
  /** O endereço que o agente usa no `enroll --url` (o que o servidor confere na assinatura). */
  server_url: string;
};

type MachinePage = {
  items: Machine[];
  total: number;
  page: number;
  per_page: number;
  revoked_count: number;
};

export const MODE_LABEL: Record<MachineMode, string> = {
  service: "Serviço",
  session: "Sessão",
  oneshot: "Execução única",
};

/** Mesmo formato que a API aceita (`MACHINE_NAME_PATTERN`). */
export const MACHINE_NAME = /^[a-z0-9][a-z0-9-]{0,62}$/;

export const POLL_MS = 15_000;
const PAGE_SIZE = 50; // o maior tamanho de página que a API aceita

export const machinesKey = ["machines"] as const;
export const poolsKey = ["pools"] as const;

/**
 * Todas as máquinas do contexto (a tela agrupa por pool, então precisa da lista inteira, não de
 * uma página). Percorre as páginas do servidor até completar o total.
 */
export function useAllMachines(includeRevoked: boolean, contextKey: string) {
  return useQuery({
    queryKey: [...machinesKey, "all", includeRevoked, contextKey],
    refetchInterval: POLL_MS,
    queryFn: async () => {
      const items: Machine[] = [];
      let revokedCount = 0;
      for (let page = 1; page <= 40; page += 1) {
        const query = new URLSearchParams({
          page: String(page),
          per_page: String(PAGE_SIZE),
          sort: "name",
          include_revoked: String(includeRevoked),
        });
        const result = await api.get<MachinePage>(`/machines?${query}`);
        items.push(...result.items);
        revokedCount = result.revoked_count;
        if (items.length >= result.total || result.items.length === 0) break;
      }
      return { items, revokedCount };
    },
  });
}

export function usePools(contextKey: string) {
  return useQuery({
    queryKey: [...poolsKey, contextKey],
    refetchInterval: POLL_MS,
    queryFn: () => api.get<{ items: Pool[] }>("/pools").then((result) => result.items),
  });
}

/** "há 8s", "há 3 min", "há 2 h": o `relativeTime` do painel não chega a segundos. */
export function ago(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  return seconds < 60 ? `há ${seconds}s` : relativeTime(iso, now);
}

function dayAndTime(iso: string, now: number, joiner = " "): string {
  const time = formatListDate(iso).slice(-5);
  if (formatDate(iso) === formatDate(new Date(now).toISOString())) return `hoje${joiner}${time}`;
  if (formatDate(iso) === formatDate(new Date(now - 24 * 3600 * 1000).toISOString())) {
    return `ontem${joiner}${time}`;
  }
  return formatListDate(iso);
}

/**
 * Coluna "Último sinal": "há 8s" quando está online, "hoje 08:20 · há 2 h" sem sinal, e para a
 * máquina que ainda não se cadastrou, o estado da chave (design-system.md 7.13 e §13).
 */
export function lastSignal(machine: Machine, now: number = Date.now()): string {
  if (machine.status === "pending") {
    const expired = !machine.key_expires_at || new Date(machine.key_expires_at).getTime() <= now;
    if (expired) return "Chave expirada, gere uma nova";
    return machine.key_created_at
      ? `Chave gerada ${ago(machine.key_created_at, now)}, ainda não usada`
      : "Chave gerada, ainda não usada";
  }
  if (!machine.last_seen_at) return "—";
  if (machine.status === "online") return ago(machine.last_seen_at, now);
  return `${dayAndTime(machine.last_seen_at, now)} · ${ago(machine.last_seen_at, now)}`;
}

/**
 * Texto do banner de máquina sem sinal (design-system.md §13). O mesmo caso tem o mesmo texto em
 * todas as telas: o que acontece com as execuções do pool, o que conferir e, no modo Sessão, o
 * usuário do Windows.
 */
export function silenceText(mode: MachineMode, otherOnline: string | null | undefined): string {
  const effect = otherOnline
    ? `Até o agente voltar, as execuções do pool vão para a máquina ${otherOnline}. `
    : "Até o agente voltar, as execuções do pool ficam pendentes. ";
  const session = mode === "session" ? " Confira também se o usuário do Windows continua logado." : "";
  return `${effect}Confira se o computador está ligado e com internet.${session}`;
}

/** Frase do cabeçalho do detalhe: "Último sinal hoje às 08:20, há 2 h". */
export function lastSignalSentence(machine: Machine, now: number = Date.now()): string {
  if (machine.status === "pending") return lastSignal(machine, now);
  if (!machine.last_seen_at) return "Ainda sem sinal";
  if (machine.status === "online") return `Último sinal ${ago(machine.last_seen_at, now)}`;
  const when = dayAndTime(machine.last_seen_at, now, " às ");
  return `Último sinal ${when}, ${ago(machine.last_seen_at, now)}`;
}

export type MachineEventKind =
  | "enrolled"
  | "re_enrolled"
  | "first_signal"
  | "went_offline"
  | "came_back"
  | "agent_updated"
  | "revoked";

export type MachineEvent = {
  id: string;
  kind: MachineEventKind;
  created_at: string;
  metadata: Record<string, unknown>;
};

/** Texto curto de um valor vindo do agente: não confiável, então limitado (o React escapa o resto). */
function shown(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value).slice(0, 40) : "—";
}

/** Título, detalhe e tom de cada evento do histórico (design-system.md 7.14 e §13). */
export function describeEvent(event: MachineEvent): {
  title: string;
  detail?: string;
  tone: "success" | "danger" | "neutral" | "accent";
} {
  const meta = event.metadata;
  switch (event.kind) {
    case "enrolled":
      return {
        title: "Máquina cadastrada",
        detail: meta.agent_version ? `Agente ${shown(meta.agent_version)}` : undefined,
        tone: "accent",
      };
    case "re_enrolled":
      return {
        title: "Agente cadastrado de novo",
        detail: "O agente anterior deixou de funcionar.",
        tone: "accent",
      };
    case "first_signal":
      return { title: "Ficou online pela primeira vez", tone: "success" };
    case "came_back":
      return { title: "Voltou a ficar online", tone: "success" };
    case "went_offline":
      return { title: "Ficou sem sinal", tone: "danger" };
    case "agent_updated":
      return {
        title: "Agente atualizado",
        detail: `${shown(meta.from)} → ${shown(meta.to)}`,
        tone: "neutral",
      };
    case "revoked":
      return { title: "Máquina revogada", tone: "neutral" };
  }
}

export type MachinesSummary = {
  total: number;
  online: number;
  no_signal: number;
  clients: { client_id: string; client_name: string; no_signal: number }[];
};

/** Contagem de máquinas sem sinal: alimenta o contador da sidebar e o seletor de cliente. */
export function useMachinesSummary() {
  return useQuery({
    queryKey: [...machinesKey, "summary"],
    refetchInterval: POLL_MS,
    queryFn: () => api.get<MachinesSummary>("/machines/summary"),
  });
}
