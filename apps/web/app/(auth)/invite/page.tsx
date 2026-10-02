"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api";
import { messageFor } from "@/lib/messages";
import { ME_KEY } from "@/lib/me";

type Invitation = { state: "loading" } | { state: "invalid" } | { state: "ready"; email: string };

// O token vem no fragmento (#...), que o navegador nunca envia a servidor, proxy ou log de acesso.
// Ele é lido uma vez, o fragmento sai da barra de endereço e o token fica só em memória.
export default function InvitePage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const token = useRef("");
  const [invitation, setInvitation] = useState<Invitation>({ state: "loading" });
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);

  const started = useRef(false);

  useEffect(() => {
    // O efeito roda duas vezes em dev (modo estrito): a segunda já não encontra o fragmento.
    if (started.current) return;
    started.current = true;
    token.current = window.location.hash.replace(/^#/, "");
    window.history.replaceState(null, "", window.location.pathname);
    if (!token.current) {
      setInvitation({ state: "invalid" });
      return;
    }
    api
      .post<{ email: string }>("/auth/invitations/inspect", { token: token.current })
      .then(({ email }) => setInvitation({ state: "ready", email }))
      .catch(() => setInvitation({ state: "invalid" }));
  }, []);

  const accept = useMutation({
    mutationFn: () =>
      api.post("/auth/invitations/accept", { token: token.current, password }),
    onSuccess: () => {
      token.current = "";
      queryClient.removeQueries({ queryKey: ME_KEY });
      router.replace("/login/mfa/setup");
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 404) setInvitation({ state: "invalid" });
      else setError(messageFor(err));
    },
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (password !== repeat) {
      setError("As senhas não são iguais.");
      return;
    }
    setError(null);
    accept.mutate();
  }

  if (invitation.state === "loading") {
    return (
      <div className="grid gap-4" aria-busy>
        <Skeleton className="h-9 w-48" />
        <Skeleton className="h-11" />
        <Skeleton className="h-11" />
      </div>
    );
  }

  if (invitation.state === "invalid") {
    return (
      <EmptyState
        tone="error"
        title="Este convite não vale mais"
        description="O link expirou ou já foi usado. Peça um novo convite ao administrador do seu escritório."
      />
    );
  }

  return (
    <form onSubmit={submit} className="grid gap-5" noValidate>
      <header>
        <h1 className="text-display max-[899px]:text-[24px]">Crie sua senha</h1>
        <p className="mt-1 text-body text-text-label">
          Você vai entrar no Regista com o e-mail{" "}
          <span className="font-medium text-text">{invitation.email}</span> e esta senha.
        </p>
      </header>

      <Field
        label="Nova senha"
        help="Pelo menos 12 caracteres. Evite senhas comuns."
        error={error && error !== "As senhas não são iguais." ? error : null}
      >
        {(control) => (
          <Input
            {...control}
            type="password"
            autoComplete="new-password"
            autoFocus
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        )}
      </Field>
      <Field label="Repita a senha" error={error === "As senhas não são iguais." ? error : null}>
        {(control) => (
          <Input
            {...control}
            type="password"
            autoComplete="new-password"
            required
            value={repeat}
            onChange={(event) => setRepeat(event.target.value)}
          />
        )}
      </Field>

      <Button
        type="submit"
        size="lg"
        className="w-full"
        loading={accept.isPending}
        loadingText="Criando…"
      >
        Criar senha
      </Button>
    </form>
  );
}
