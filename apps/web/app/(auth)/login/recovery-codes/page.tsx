"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Banner } from "@/components/Banner";
import { CopyButton } from "@/components/CopyButton";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { messageFor } from "@/lib/messages";
import { ME_KEY, useMe, useStageGuard } from "@/lib/me";
import { clearRecoveryCodes, holdRecoveryCodes, peekRecoveryCodes } from "@/lib/recovery-store";

// Dois usos da mesma tela (design-system.md 7.1 e 7.18): no primeiro acesso (etapa
// `recovery_codes`) e depois de "Gerar novos códigos" em Minhas sessões (`?from=account`).
export default function RecoveryCodesPage() {
  return (
    <Suspense>
      <RecoveryCodes />
    </Suspense>
  );
}

function RecoveryCodes() {
  const fromAccount = useSearchParams().get("from") === "account";
  return fromAccount ? <FromAccount /> : <FirstAccess />;
}

function CodesView({
  codes,
  stepLabel,
  confirmLabel,
  loadingLabel,
  pending,
  onConfirm,
}: Readonly<{
  codes: string[] | null;
  stepLabel?: string;
  confirmLabel: string;
  loadingLabel: string;
  pending: boolean;
  onConfirm: () => void;
}>) {
  const [saved, setSaved] = useState(false);
  return (
    <div className="grid gap-5">
      <header>
        {stepLabel && <p className="text-overline uppercase text-accent-text">{stepLabel}</p>}
        <h1 className="mt-1 text-display max-[899px]:text-[24px]">
          Guarde seus códigos de recuperação
        </h1>
        <p className="mt-1 text-body text-text-label">
          Se perder o celular, entre com um destes códigos. Cada um funciona uma vez.
        </p>
      </header>

      <Banner tone="warning" title="Eles não serão mostrados de novo">
        Copie agora e guarde num gerenciador de senhas.
      </Banner>

      {codes ? (
        <ul className="grid grid-cols-2 gap-x-6 gap-y-2 rounded-card border border-border bg-panel p-4 font-mono text-body">
          {codes.map((code) => (
            <li key={code}>{code}</li>
          ))}
        </ul>
      ) : (
        <Skeleton className="h-[172px]" />
      )}

      <CopyButton
        value={(codes ?? []).join("\n")}
        label="Copiar códigos"
        copiedLabel="Copiados"
        className="w-fit"
      />

      <label className="flex items-center gap-1 text-body-sm text-text-secondary">
        <Checkbox checked={saved} onCheckedChange={(value) => setSaved(value === true)} />
        Guardei os códigos em um lugar seguro
      </label>

      <Button
        size="lg"
        className="w-full"
        disabled={!saved || !codes}
        loading={pending}
        loadingText={loadingLabel}
        onClick={onConfirm}
      >
        {confirmLabel}
      </Button>
    </div>
  );
}

function FirstAccess() {
  const { ready } = useStageGuard("recovery_codes");
  const router = useRouter();
  const queryClient = useQueryClient();
  const [codes, setCodes] = useState<string[] | null>(() => peekRecoveryCodes());
  const [error, setError] = useState<string | null>(null);
  const requested = useRef(false);

  // Se a página foi recarregada, os códigos em memória se perderam: gera um conjunto novo.
  useEffect(() => {
    if (!ready || codes || requested.current) return;
    requested.current = true;
    api
      .post<{ recovery_codes: string[] }>("/auth/recovery-codes/reissue")
      .then(({ recovery_codes }) => {
        holdRecoveryCodes(recovery_codes);
        setCodes(recovery_codes);
      })
      .catch((err) => setError(messageFor(err)));
  }, [ready, codes]);

  const finish = useMutation({
    mutationFn: () => api.post("/auth/recovery-codes/ack"),
    onSuccess: () => {
      clearRecoveryCodes();
      queryClient.removeQueries({ queryKey: ME_KEY });
      router.replace("/");
    },
    onError: (err) => setError(messageFor(err)),
  });

  if (!ready) return null;
  return (
    <>
      <CodesView
        codes={codes}
        stepLabel="Passo 2 de 2"
        confirmLabel="Concluir e entrar"
        loadingLabel="Entrando…"
        pending={finish.isPending}
        onConfirm={() => finish.mutate()}
      />
      {error && (
        <p role="alert" className="mt-3 text-caption text-danger-text">
          {error}
        </p>
      )}
    </>
  );
}

function FromAccount() {
  const router = useRouter();
  const me = useMe();
  const [codes] = useState<string[] | null>(() => peekRecoveryCodes());

  useEffect(() => {
    if (me.isError) router.replace("/login");
    else if (me.isSuccess && (me.data.stage !== "active" || !codes)) {
      router.replace(me.data.stage === "active" ? "/account/sessions" : "/login");
    }
  }, [me.isError, me.isSuccess, me.data, codes, router]);

  if (!codes) return null;
  return (
    <CodesView
      codes={codes}
      confirmLabel="Concluir"
      loadingLabel="Concluindo…"
      pending={false}
      onConfirm={() => {
        clearRecoveryCodes();
        router.replace("/account/sessions");
      }}
    />
  );
}
