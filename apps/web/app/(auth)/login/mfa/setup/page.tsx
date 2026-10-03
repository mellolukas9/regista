"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { useRouter } from "next/navigation";
import { Banner } from "@/components/Banner";
import { CopyButton } from "@/components/CopyButton";
import { Field } from "@/components/Field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api";
import { messageFor } from "@/lib/messages";
import { ME_KEY, useStageGuard } from "@/lib/me";
import { holdRecoveryCodes } from "@/lib/recovery-store";

type Setup = { secret: string; otpauth_uri: string };

/** Chave agrupada de 4 em 4 para digitar no app. */
function grouped(secret: string): string {
  return secret.replace(/(.{4})/g, "$1 ").trim();
}

export default function MfaSetupPage() {
  const { ready } = useStageGuard("mfa_setup");
  const router = useRouter();
  const queryClient = useQueryClient();
  const [setup, setSetup] = useState<Setup | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const requested = useRef(false);

  // Cada chamada gera um segredo novo; no modo estrito do React o efeito roda duas vezes em dev,
  // então a chamada é protegida para a tela e o servidor terminarem com o mesmo segredo.
  useEffect(() => {
    if (!ready || requested.current) return;
    requested.current = true;
    api
      .post<Setup>("/auth/mfa/setup")
      .then(setSetup)
      .catch((err) => setLoadError(messageFor(err)));
  }, [ready]);

  const activate = useMutation({
    mutationFn: () => api.post<{ recovery_codes: string[] }>("/auth/mfa/activate", { code }),
    onSuccess: ({ recovery_codes }) => {
      holdRecoveryCodes(recovery_codes);
      queryClient.removeQueries({ queryKey: ME_KEY });
      router.replace("/login/recovery-codes");
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 401) {
        setError(
          "Código incorreto ou expirado. Os códigos mudam a cada 30 segundos; use o que está na tela agora.",
        );
      } else {
        setError(messageFor(err));
      }
    },
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    activate.mutate();
  }

  if (!ready) return null;

  return (
    <form onSubmit={submit} className="grid gap-5" noValidate>
      <header>
        <p className="text-overline uppercase text-accent-text">Passo 1 de 2</p>
        <h1 className="mt-1 text-display max-[899px]:text-[24px]">Proteja sua conta</h1>
        <p className="mt-1 text-body text-text-label">
          Todo acesso ao Regista pede um código do celular, além da senha.
        </p>
      </header>

      <ol className="grid gap-2 text-body-sm text-text-secondary">
        <li>
          <span className="mr-2 font-mono text-text-label">1.</span>
          Instale um app autenticador, como Google Authenticator ou Microsoft Authenticator.
        </li>
        <li>
          <span className="mr-2 font-mono text-text-label">2.</span>
          No app, escolha adicionar conta e aponte a câmera para o código abaixo.
        </li>
      </ol>

      {loadError && (
        <Banner tone="danger" title="Não foi possível gerar o código">
          {loadError}
        </Banner>
      )}

      {setup ? (
        <div className="grid items-center gap-4 min-[420px]:grid-cols-[auto_1fr]">
          <div className="w-fit rounded-control bg-white p-3">
            <QRCodeSVG
              value={setup.otpauth_uri}
              size={148}
              level="M"
              bgColor="#FFFFFF"
              fgColor="#000000"
              title="Código QR para o app autenticador"
            />
          </div>
          <div className="grid gap-2">
            <p className="text-body-sm text-text-label">
              Não consegue escanear? Digite esta chave no app:
            </p>
            <p className="break-all font-mono text-body text-text">{grouped(setup.secret)}</p>
            <CopyButton value={setup.secret} label="Copiar chave" copiedLabel="Copiada" className="w-fit" />
          </div>
        </div>
      ) : (
        !loadError && <Skeleton className="h-[172px]" />
      )}

      <Field label="Código de 6 dígitos que aparece no app" error={error}>
        {(control) => (
          <Input
            {...control}
            required
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            className="font-mono"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
        )}
      </Field>

      <Button
        type="submit"
        size="lg"
        className="w-full"
        disabled={!setup}
        loading={activate.isPending}
        loadingText="Ativando…"
      >
        Ativar verificação
      </Button>
    </form>
  );
}
