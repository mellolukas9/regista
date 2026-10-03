"use client";

import { useCallback } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

export type ListState = { page: number; perPage: number; sort: string | null; q: string };

const PER_PAGE_CHOICES = [10, 25, 50];

/**
 * Filtros, ordenação e paginação ficam na URL (design-system.md, regra 9): `?q=&sort=-name&page=2`.
 * O processamento é no servidor; aqui só lemos e escrevemos a query string.
 */
export function useListState() {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();

  const page = Math.max(1, Number(search.get("page")) || 1);
  const rawPerPage = Number(search.get("per_page"));
  const perPage = PER_PAGE_CHOICES.includes(rawPerPage) ? rawPerPage : 25;
  const state: ListState = {
    page,
    perPage,
    sort: search.get("sort"),
    q: search.get("q") ?? "",
  };

  const update = useCallback(
    (changes: Partial<ListState>) => {
      const next = new URLSearchParams(search.toString());
      for (const [key, value] of Object.entries(changes)) {
        const name = key === "perPage" ? "per_page" : key;
        if (value === null || value === "" || value === undefined) next.delete(name);
        else next.set(name, String(value));
      }
      // Mudou filtro, busca, ordem ou tamanho da página: volta para a primeira página.
      if (!("page" in changes)) next.delete("page");
      const query = next.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router, search],
  );

  return { state, update };
}

/** Parâmetros da API a partir do estado da URL. */
export function listQuery(state: ListState, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams({
    page: String(state.page),
    per_page: String(state.perPage),
    ...extra,
  });
  if (state.sort) params.set("sort", state.sort);
  if (state.q) params.set("q", state.q);
  return params.toString();
}
