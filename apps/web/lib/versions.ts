"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { botsKey } from "@/lib/bots";
import { POLL_MS } from "@/lib/machines";

export type BotVersion = {
  id: string;
  version: string;
  package_sha256: string;
  size_bytes: number;
  release_note: string | null;
  published_at: string;
  published_by: string;
  is_current: boolean;
  python: string;
  playwright: string | null;
  chromium_revision: string | null;
};

export type BotVersionList = { items: BotVersion[]; current_version_id: string | null };

export type UploadTicket = {
  version_id: string;
  version: string;
  upload_url: string;
  upload_headers: Record<string, string>;
  expires_in: number;
};

export const versionsKey = (botId: string) => [...botsKey, "versions", botId] as const;

export function useBotVersions(botId: string, contextKey: string) {
  return useQuery({
    queryKey: [...versionsKey(botId), contextKey],
    queryFn: () => api.get<BotVersionList>(`/bots/${botId}/versions`),
    refetchInterval: POLL_MS,
  });
}

/** Hash curto da lista (design-system.md 7.6): `3f9a…c21e`. O hash inteiro fica no Tooltip. */
export function shortHash(sha256: string): string {
  return `${sha256.slice(0, 4)}…${sha256.slice(-4)}`;
}
