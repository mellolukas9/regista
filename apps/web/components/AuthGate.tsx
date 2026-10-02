"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { MeProvider } from "@/lib/me-context";
import { stagePath, useMe } from "@/lib/me";

/**
 * Só deixa passar quem tem sessão ativa. A decisão é da API (GET /auth/me); o cookie não diz nada
 * ao navegador. Sem sessão vai para o login, no meio do login volta para a etapa em que parou.
 */
export function AuthGate({ children }: Readonly<{ children: React.ReactNode }>) {
  const me = useMe();
  const router = useRouter();
  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;
  const wrongStage = me.isSuccess && me.data.stage !== "active";

  useEffect(() => {
    if (unauthenticated) router.replace("/login");
    else if (me.isSuccess && me.data.stage !== "active") router.replace(stagePath(me.data.stage));
  }, [unauthenticated, me.isSuccess, me.data, router]);

  if (me.isError && !unauthenticated) {
    return (
      <div className="mx-auto max-w-[520px] px-4 py-24">
        <EmptyState
          tone="error"
          title="Não foi possível carregar o painel"
          description="O servidor não respondeu. Seus robôs continuam rodando; só o painel está sem dados agora."
          action={
            <Button variant="secondary" onClick={() => me.refetch()}>
              Tentar de novo
            </Button>
          }
        />
      </div>
    );
  }

  if (!me.isSuccess || wrongStage) {
    return (
      <div className="grid gap-4 p-10" aria-busy>
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-32" />
      </div>
    );
  }

  return (
    <MeProvider me={me.data}>
      <AppShell>{children}</AppShell>
    </MeProvider>
  );
}
