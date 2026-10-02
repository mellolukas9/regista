"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Banner } from "@/components/Banner";
import { Field } from "@/components/Field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { ME_KEY, useStageGuard } from "@/lib/me";

type Mode = "totp" | "recovery";

const COPY: Record<Mode, { title: string; subtitle: string; label: string; error: string }> = {
  totp: {
    title: "Digite o código",
    subtitle: "Abra o app autenticador e digite o código de 6 dígitos do Regista.",
    label: "Código",
    error:
      "Código incorreto ou expirado. Os códigos mudam a cada 30 segundos; use o que está na tela agora.",
  },
  recovery: {
    title: "Use um código de recuperação",
    subtitle:
      "Digite um dos códigos que você guardou ao ativar a verificação. Cada um funciona uma vez.",
    label: "Código de recuperação",
    error: "Código de recuperação inválido ou já usado.",
  },
};

export default function MfaPage() {
  const { ready } = useStageGuard("mfa_required");
  const router = useRouter();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<Mode>("totp");
  const [code, setCode] = useState("");
  const [failure, setFailure] = useState<"code" | "limited" | "other" | null>(null);

  const verify = useMutation({
    mutationFn: () =>
      mode === "totp"
        ? api.post("/auth/mfa/verify", { code })
        : api.post("/auth/mfa/recover", { recovery_code: code }),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ME_KEY });
      router.replace("/");
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) setFailure("code");
      else if (error instanceof ApiError && error.status === 429) setFailure("limited");
      else setFailure("other");
    },
  });

  const back = useMutation({
    mutationFn: () => api.post("/auth/logout"),
    onSettled: () => {
      queryClient.removeQueries({ queryKey: ME_KEY });
      router.replace("/login");
    },
  });

  function switchMode(next: Mode) {
    setMode(next);
    setCode("");
    setFailure(null);
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setFailure(null);
    verify.mutate();
  }

  if (!ready) return null;
  const copy = COPY[mode];

  return (
    <form onSubmit={submit} className="grid gap-5" noValidate>
      <header>
        <h1 className="text-display max-[899px]:text-[24px]">{copy.title}</h1>
        <p className="mt-1 text-body text-text-label">{copy.subtitle}</p>
      </header>

      {failure === "limited" && (
        <Banner tone="danger" title="Muitas tentativas">
          Espere alguns minutos e tente de novo. Se esqueceu a senha, peça ao administrador do seu
          escritório.
        </Banner>
      )}

      <Field label={copy.label} error={failure === "code" ? copy.error : null}>
        {(control) => (
          <Input
            {...control}
            key={mode}
            autoFocus
            required
            autoComplete="one-time-code"
            inputMode={mode === "totp" ? "numeric" : "text"}
            maxLength={mode === "totp" ? 6 : 12}
            className={mode === "recovery" ? "font-mono uppercase" : "font-mono"}
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
        )}
      </Field>
      {failure === "other" && (
        <p role="alert" className="text-caption text-danger-text">
          O servidor não respondeu. Tente de novo em alguns segundos.
        </p>
      )}

      <Button
        type="submit"
        size="lg"
        className="w-full"
        loading={verify.isPending}
        loadingText="Confirmando…"
      >
        Confirmar
      </Button>

      <div className="flex flex-wrap items-center justify-between gap-3 text-body-sm">
        <button
          type="button"
          onClick={() => back.mutate()}
          className="min-h-11 text-accent-text hover:text-accent-text-hover"
        >
          Voltar
        </button>
        <button
          type="button"
          onClick={() => switchMode(mode === "totp" ? "recovery" : "totp")}
          className="min-h-11 text-accent-text hover:text-accent-text-hover"
        >
          {mode === "totp" ? "Usar código de recuperação" : "Usar o app autenticador"}
        </button>
      </div>
    </form>
  );
}
