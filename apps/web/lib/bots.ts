"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { POLL_MS } from "@/lib/machines";
import type { JobStatus } from "@/lib/status";

export type LastRun = {
  id: string;
  short_code: string;
  status: JobStatus;
  created_at: string;
  finished_at: string | null;
};

export type Bot = {
  id: string;
  name: string;
  package_name: string;
  description: string | null;
  pool_id: string;
  pool_name: string;
  client_id: string;
  client_name: string;
  is_active: boolean;
  created_at: string;
  last_run: LastRun | null;
  /** Das últimas 10 execuções, da mais antiga para a mais nova. */
  recent_statuses: JobStatus[];
  recent_ids: string[];
  has_active_run: boolean;
};

export type BotDetail = Bot & {
  machines_total: number;
  machines_online: number;
  runs_30d: number;
  success_rate_30d: number | null;
};

export type BotPage = { items: Bot[]; total: number; page: number; per_page: number };

/** Mesmo formato que a API aceita (`PACKAGE_NAME_PATTERN`). */
export const PACKAGE_NAME = /^[a-z][a-z0-9_]{0,62}$/;

export const botsKey = ["bots"] as const;

/** Todos os bots do contexto (para os filtros e seletores; a lista da tela é paginada no servidor). */
export function useBotOptions(contextKey: string) {
  return useQuery({
    queryKey: [...botsKey, "options", contextKey],
    refetchInterval: POLL_MS,
    queryFn: () => api.get<BotPage>("/bots?per_page=50&sort=name").then((page) => page.items),
  });
}

export function successSentence(bot: Pick<BotDetail, "runs_30d" | "success_rate_30d">): string {
  if (bot.success_rate_30d === null) return "Sem execuções nos últimos 30 dias";
  return `${Math.round(bot.success_rate_30d * 100)}% das execuções`;
}
