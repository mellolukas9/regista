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
};

export type IssuedKey = {
  machine_id: string;
  name: string;
  enrollment_key: string;
  expires_at: string;
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

function dayAndTime(iso: string, now: number): string {
  const time = formatListDate(iso).slice(-5);
  if (formatDate(iso) === formatDate(new Date(now).toISOString())) return `hoje ${time}`;
  if (formatDate(iso) === formatDate(new Date(now - 24 * 3600 * 1000).toISOString())) {
    return `ontem ${time}`;
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
