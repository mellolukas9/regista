"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Laptop, Smartphone } from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api";
import { formatDate, formatDateTime, relativeTime } from "@/lib/format";
import { messageFor } from "@/lib/messages";
import { ME_KEY } from "@/lib/me";
import { useCurrentUser } from "@/lib/me-context";
import { holdRecoveryCodes } from "@/lib/recovery-store";

type Session = {
  id: string;
  device: string;
  ip: string | null;
  created_at: string;
  last_seen_at: string;
  is_current: boolean;
};

const SESSIONS_KEY = ["account", "sessions"] as const;
const TOTAL_CODES = 10;

export default function SessionsPage() {
  const me = useCurrentUser();
  const queryClient = useQueryClient();
  const [endingOthers, setEndingOthers] = useState(false);
  const [othersError, setOthersError] = useState<string | null>(null);

  const sessions = useQuery({
    queryKey: SESSIONS_KEY,
    queryFn: () => api.get<{ items: Session[] }>("/account/sessions"),
  });

  const endOne = useMutation({
    mutationFn: (id: string) => api.delete(`/account/sessions/${id}`),
    onSuccess: () => {
      toast.success("Sessão encerrada");
      queryClient.invalidateQueries({ queryKey: SESSIONS_KEY });
    },
    onError: (error) => toast.error(messageFor(error), { duration: Infinity, closeButton: true }),
  });

  const endOthers = useMutation({
    mutationFn: () => api.post<{ revoked: number }>("/account/sessions/revoke-others"),
    onSuccess: () => {
      toast.success("Outras sessões encerradas");
      queryClient.invalidateQueries({ queryKey: SESSIONS_KEY });
      setEndingOthers(false);
    },
    onError: (error) => setOthersError(messageFor(error)),
  });

  const items = sessions.data?.items ?? [];
  const hasOthers = items.some((session) => !session.is_current);

  return (
    <>
      <PageHeader
        title="Minhas sessões"
        description={`Aparelhos conectados à sua conta ${me.email}. Se não reconhecer algum, encerre e troque a senha.`}
        actions={
          <Button variant="secondary" disabled={!hasOthers} onClick={() => setEndingOthers(true)}>
            Encerrar todas as outras
          </Button>
        }
      />

      {sessions.isPending ? (
        <div className="grid gap-3" aria-busy>
          <Skeleton className="h-[72px]" />
          <Skeleton className="h-[72px]" />
        </div>
      ) : sessions.isError ? (
        <EmptyState
          tone="error"
          title="Não foi possível carregar as sessões"
          description="O servidor não respondeu. Tente de novo em alguns segundos."
          action={
            <Button variant="secondary" onClick={() => sessions.refetch()}>
              Tentar de novo
            </Button>
          }
        />
      ) : (
        <ul className="grid gap-3">
          {items.map((session) => {
            const mobile = /Android|iOS/.test(session.device);
            const Icon = mobile ? Smartphone : Laptop;
            return (
              <li
                key={session.id}
                className="flex flex-wrap items-center gap-4 rounded-card border border-border bg-panel p-4"
              >
                <span
                  aria-hidden
                  className="grid size-11 shrink-0 place-items-center rounded-control bg-panel-active text-text-label"
                >
                  <Icon className="size-5" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-title text-text">{session.device}</p>
                    {session.is_current && <Badge variant="accent">Esta sessão</Badge>}
                  </div>
                  <p className="text-body-sm text-text-label" title={formatDateTime(session.last_seen_at)}>
                    {session.ip ?? "Endereço desconhecido"} ·{" "}
                    {session.is_current ? "ativa agora" : `último uso ${relativeTime(session.last_seen_at)}`}
                  </p>
                </div>
                {!session.is_current && (
                  <Button
                    variant="secondary"
                    loading={endOne.isPending && endOne.variables === session.id}
                    loadingText="Encerrando…"
                    onClick={() => endOne.mutate(session.id)}
                  >
                    Encerrar
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <section id="verificacao" className="scroll-mt-6">
        <TwoFactorCard />
      </section>
      <PasswordCard />

      <ConfirmDialog
        open={endingOthers}
        onOpenChange={(next) => {
          if (!next) {
            setEndingOthers(false);
            setOthersError(null);
          }
        }}
        title="Encerrar todas as outras sessões?"
        description="Os outros aparelhos saem do Regista e precisam entrar de novo. Robôs em execução não param."
        confirmLabel="Encerrar todas as outras"
        loadingLabel="Encerrando…"
        pending={endOthers.isPending}
        error={othersError}
        onConfirm={() => endOthers.mutate()}
      />
    </>
  );
}

function TwoFactorCard() {
  const me = useCurrentUser();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const regenerate = useMutation({
    mutationFn: () =>
      api.post<{ recovery_codes: string[] }>("/account/recovery-codes", { password }),
    onSuccess: ({ recovery_codes }) => {
      holdRecoveryCodes(recovery_codes);
      queryClient.invalidateQueries({ queryKey: ME_KEY });
      router.push("/login/recovery-codes?from=account");
    },
    onError: (err) => setError(messageFor(err)),
  });

  function close() {
    setOpen(false);
    setPassword("");
    setError(null);
  }

  const remaining = me.recovery_codes_remaining ?? 0;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Verificação em duas etapas</CardTitle>
          <CardDescription>
            {me.mfa_enabled_at ? `Ativada em ${formatDate(me.mfa_enabled_at)} com app autenticador. ` : ""}
            Restam {remaining} de {TOTAL_CODES} códigos de recuperação.
          </CardDescription>
        </div>
        <Badge variant="success-text">Ativa</Badge>
      </CardHeader>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button variant="secondary" onClick={() => setOpen(true)}>
          Gerar novos códigos de recuperação
        </Button>
        <p className="text-caption text-text-label">Gerar novos códigos invalida os antigos.</p>
      </div>

      <Dialog open={open} onOpenChange={(next) => !next && !regenerate.isPending && close()}>
        <DialogContent>
          <form
            className="grid gap-5"
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              setError(null);
              regenerate.mutate();
            }}
          >
            <DialogHeader>
              <DialogTitle>Gerar novos códigos de recuperação?</DialogTitle>
              <DialogDescription>
                Os códigos atuais deixam de valer. Confirme sua senha para continuar.
              </DialogDescription>
            </DialogHeader>
            <Field label="Senha" error={error}>
              {(control) => (
                <Input
                  {...control}
                  type="password"
                  autoComplete="current-password"
                  autoFocus
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              )}
            </Field>
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={close} disabled={regenerate.isPending}>
                Voltar
              </Button>
              <Button
                type="submit"
                disabled={!password}
                loading={regenerate.isPending}
                loadingText="Gerando…"
              >
                Gerar novos códigos
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function PasswordCard() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [code, setCode] = useState("");
  const [errors, setErrors] = useState<{ current?: string; next?: string; repeat?: string; code?: string; form?: string }>({});

  function close() {
    setOpen(false);
    setCurrent("");
    setNext("");
    setRepeat("");
    setCode("");
    setErrors({});
  }

  const change = useMutation({
    mutationFn: () =>
      api.post("/account/password", { current_password: current, new_password: next, code }),
    onSuccess: () => {
      toast.success("Senha alterada. Os outros aparelhos foram desconectados.");
      queryClient.invalidateQueries({ queryKey: SESSIONS_KEY });
      close();
    },
    onError: (err) => {
      if (!(err instanceof ApiError)) return setErrors({ form: messageFor(err) });
      switch (err.code) {
        case "password_incorrect":
          return setErrors({ current: messageFor(err) });
        case "invalid_code":
          return setErrors({ code: messageFor(err) });
        case "too_short":
        case "too_common":
        case "same_as_current":
          return setErrors({ next: messageFor(err) });
        default:
          return setErrors({ form: messageFor(err) });
      }
    },
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (next !== repeat) {
      setErrors({ repeat: "As senhas não são iguais." });
      return;
    }
    setErrors({});
    change.mutate();
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Senha</CardTitle>
          <CardDescription>
            Ao trocar, você continua conectado aqui e sai dos outros aparelhos.
          </CardDescription>
        </div>
        <Button variant="secondary" onClick={() => setOpen(true)}>
          Trocar senha
        </Button>
      </CardHeader>

      <Dialog open={open} onOpenChange={(value) => !value && !change.isPending && close()}>
        <DialogContent>
          <form onSubmit={submit} className="grid gap-5" noValidate>
            <DialogHeader>
              <DialogTitle>Trocar senha</DialogTitle>
              <DialogDescription>
                Pedimos também o código do app autenticador, para que uma sessão roubada sozinha não
                baste para tomar a conta.
              </DialogDescription>
            </DialogHeader>
            <Field label="Senha atual" error={errors.current}>
              {(control) => (
                <Input
                  {...control}
                  type="password"
                  autoComplete="current-password"
                  autoFocus
                  value={current}
                  onChange={(event) => setCurrent(event.target.value)}
                />
              )}
            </Field>
            <Field
              label="Nova senha"
              help="Pelo menos 12 caracteres. Evite senhas comuns."
              error={errors.next}
            >
              {(control) => (
                <Input
                  {...control}
                  type="password"
                  autoComplete="new-password"
                  value={next}
                  onChange={(event) => setNext(event.target.value)}
                />
              )}
            </Field>
            <Field label="Repita a nova senha" error={errors.repeat}>
              {(control) => (
                <Input
                  {...control}
                  type="password"
                  autoComplete="new-password"
                  value={repeat}
                  onChange={(event) => setRepeat(event.target.value)}
                />
              )}
            </Field>
            <Field label="Código de 6 dígitos que aparece no app" error={errors.code}>
              {(control) => (
                <Input
                  {...control}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  className="font-mono"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                />
              )}
            </Field>
            {errors.form && (
              <p role="alert" className="text-body-sm text-danger-text">
                {errors.form}
              </p>
            )}
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={close} disabled={change.isPending}>
                Voltar
              </Button>
              <Button type="submit" loading={change.isPending} loadingText="Trocando…">
                Trocar senha
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
