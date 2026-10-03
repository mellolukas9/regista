"use client";

import { createContext, useContext } from "react";
import type { Me } from "@/lib/me";

const MeContext = createContext<Me | null>(null);

/** Só existe depois que AuthGate confirmou uma sessão ativa. */
export function MeProvider({ me, children }: Readonly<{ me: Me; children: React.ReactNode }>) {
  return <MeContext.Provider value={me}>{children}</MeContext.Provider>;
}

export function useCurrentUser(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error("useCurrentUser fora de AuthGate");
  return me;
}

/** Nome do cliente em que o painel está trabalhando ("Todos os clientes" para a equipe Artemisys). */
export function contextName(me: Me): string {
  if (me.context?.all_clients) return "Todos os clientes";
  return me.context?.client_name ?? me.tenant_name ?? "";
}
