"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Banner } from "@/components/Banner";
import { Field } from "@/components/Field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { ME_KEY, stagePath, useStageGuard, type Stage } from "@/lib/me";

type Failure = "credentials" | "limited" | "other" | null;

export default function LoginPage() {
  useStageGuard("none");
  const router = useRouter();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [failure, setFailure] = useState<Failure>(null);

  const login = useMutation({
    mutationFn: () => api.post<{ stage: Stage }>("/auth/login", { email, password }),
    onSuccess: ({ stage }) => {
      queryClient.removeQueries({ queryKey: ME_KEY });
      router.replace(stagePath(stage));
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) setFailure("credentials");
      else if (error instanceof ApiError && error.status === 429) setFailure("limited");
      else setFailure("other");
    },
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setFailure(null);
    login.mutate();
  }

  return (
    <form onSubmit={submit} className="grid gap-5" noValidate>
      <header>
        <h1 className="text-display max-[899px]:text-[24px]">Entrar</h1>
        <p className="mt-1 text-body text-text-label">Use o e-mail do convite que você recebeu.</p>
      </header>

      {failure === "credentials" && (
        <Banner tone="danger" title="E-mail ou senha incorretos">
          Confira os dois e tente de novo. Se esqueceu a senha, peça ao administrador do seu
          escritório.
        </Banner>
      )}
      {failure === "limited" && (
        <Banner tone="danger" title="Muitas tentativas">
          Espere alguns minutos e tente de novo. Se esqueceu a senha, peça ao administrador do seu
          escritório.
        </Banner>
      )}
      {failure === "other" && (
        <Banner tone="danger" title="Não foi possível entrar">
          O servidor não respondeu. Tente de novo em alguns segundos.
        </Banner>
      )}

      <Field label="E-mail">
        {(control) => (
          <Input
            {...control}
            type="email"
            autoComplete="username"
            autoFocus
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        )}
      </Field>
      <Field label="Senha" invalid={failure === "credentials"}>
        {(control) => (
          <Input
            {...control}
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        )}
      </Field>

      <Button type="submit" size="lg" className="w-full" loading={login.isPending} loadingText="Entrando…">
        Entrar
      </Button>
      <p className="text-body-sm text-text-label">
        Esqueceu a senha? Fale com o administrador do seu escritório.
      </p>
    </form>
  );
}
