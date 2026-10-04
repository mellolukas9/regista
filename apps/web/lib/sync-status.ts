"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { ApiError, NETWORK_ERROR_CODE } from "@/lib/api";

export type SyncState = "ok" | "syncing" | "late" | "offline";

/** Atrasada depois de 2 min sem resposta (design-system.md §5, Topbar; §11.7). */
const LATE_AFTER_MS = 120_000;

/**
 * Indicador de sincronização da topbar: "Sincronizado · há 12s", "Sincronizando…", "Atualização
 * atrasada · há 3 min" e "Sem conexão · tentando de novo". Lê o cache do TanStack Query, que é quem
 * busca os dados a cada 15 s, e clicar força uma nova busca.
 */
export function useSyncStatus(): { state: SyncState; text: string; refresh: () => void } {
  const queryClient = useQueryClient();
  const fetching = useIsFetching() > 0;
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // "último-ok|rede-fora" muda só quando algo relevante muda, então o snapshot é estável.
  const snapshot = useSyncExternalStore(
    (notify) => queryClient.getQueryCache().subscribe(notify),
    () => {
      let lastOk = 0;
      let offline = false;
      for (const query of queryClient.getQueryCache().getAll()) {
        if (query.state.status === "success") lastOk = Math.max(lastOk, query.state.dataUpdatedAt);
        const error = query.state.error;
        if (query.state.status === "error" && error instanceof ApiError && error.code === NETWORK_ERROR_CODE) {
          offline = true;
        }
      }
      return `${lastOk}|${offline}`;
    },
    () => "0|false",
  );
  const [lastOkText, offlineText] = snapshot.split("|");
  const lastOk = Number(lastOkText);
  const offline = offlineText === "true";

  const refresh = () => {
    void queryClient.invalidateQueries();
  };

  if (offline && !fetching) return { state: "offline", text: "Sem conexão · tentando de novo", refresh };
  if (fetching) return { state: "syncing", text: "Sincronizando…", refresh };
  if (lastOk === 0) return { state: "ok", text: "Sincronizado", refresh };
  const age = Math.max(0, now - lastOk);
  if (age >= LATE_AFTER_MS) {
    return { state: "late", text: `Atualização atrasada · há ${Math.floor(age / 60_000)} min`, refresh };
  }
  return { state: "ok", text: `Sincronizado · há ${Math.floor(age / 1000)}s`, refresh };
}
