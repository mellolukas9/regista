"use client";

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export type Stage = "mfa_required" | "mfa_setup" | "recovery_codes" | "active";
export type Role = "tenant_admin" | "operator" | "viewer";

export type Me = {
  stage: Stage;
  email: string;
  role: Role | null;
  display_name: string | null;
  is_platform_admin: boolean;
  tenant_id: string | null;
  tenant_name: string | null;
  mfa_enabled: boolean;
  mfa_enabled_at: string | null;
  recovery_codes_remaining: number | null;
  context: { all_clients: boolean; client_id: string | null; client_name: string | null } | null;
};

export const ME_KEY = ["me"] as const;

export function useMe() {
  return useQuery({
    queryKey: ME_KEY,
    queryFn: () => api.get<Me>("/auth/me"),
    retry: false,
    staleTime: 30_000,
  });
}

/** Página para onde cada etapa do login leva. */
export function stagePath(stage: Stage): string {
  switch (stage) {
    case "mfa_required":
      return "/login/mfa";
    case "mfa_setup":
      return "/login/mfa/setup";
    case "recovery_codes":
      return "/login/recovery-codes";
    case "active":
      return "/";
  }
}

/**
 * Mantém a página na etapa certa do login. `expected = "none"` é a tela de entrada: quem já tem
 * sessão (parcial ou ativa) é levado para onde parou.
 */
export function useStageGuard(expected: Stage | "none") {
  const me = useMe();
  const router = useRouter();

  useEffect(() => {
    if (me.isPending) return;
    if (me.isError) {
      if (expected !== "none") router.replace("/login");
      return;
    }
    if (expected === "none" || me.data.stage !== expected) {
      router.replace(stagePath(me.data.stage));
    }
  }, [me.isPending, me.isError, me.data, expected, router]);

  const ready = expected === "none" ? !me.isPending : me.isSuccess && me.data.stage === expected;
  return { ready, me };
}

export function roleLabel(me: Pick<Me, "role" | "is_platform_admin">): string {
  if (me.is_platform_admin) return "Equipe Artemisys";
  switch (me.role) {
    case "tenant_admin":
      return "Admin do cliente";
    case "operator":
      return "Operador";
    case "viewer":
      return "Leitor";
    default:
      return "";
  }
}

export function canManageUsers(me: Pick<Me, "role" | "is_platform_admin">): boolean {
  return me.is_platform_admin || me.role === "tenant_admin";
}
