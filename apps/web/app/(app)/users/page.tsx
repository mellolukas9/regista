"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { MoreHorizontal, Search } from "lucide-react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { RadioCard, RadioGroup } from "@/components/ui/radio-group";
import { api } from "@/lib/api";
import { formatListDate } from "@/lib/format";
import { messageFor } from "@/lib/messages";
import { canManageUsers, type Role } from "@/lib/me";
import { contextName, useCurrentUser } from "@/lib/me-context";
import { listQuery, useListState } from "@/lib/url-state";

type User = {
  id: string;
  email: string;
  role: Role;
  status: "active" | "invited" | "disabled";
  mfa: "active" | "none" | null;
  last_login_at: string | null;
  client_id: string;
  client_name: string;
  is_self: boolean;
};
type UserPage = { items: User[]; total: number; page: number; per_page: number };

const ROLES: { value: Role; label: string; description: string }[] = [
  {
    value: "tenant_admin",
    label: "Admin do cliente",
    description: "Gerencia usuários, máquinas, agendamentos, alertas e retenção das filas.",
  },
  {
    value: "operator",
    label: "Operador",
    description: "Dispara robôs, reprocessa itens e envia planilhas.",
  },
  {
    value: "viewer",
    label: "Leitor",
    description: "Só consulta. Não dispara nem altera nada.",
  },
];
const ROLE_LABEL = Object.fromEntries(ROLES.map((role) => [role.value, role.label]));
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

const STATUS_LABEL: Record<User["status"], string> = {
  active: "Ativo",
  invited: "Convite enviado",
  disabled: "Removido",
};

type Action =
  | { kind: "invite" }
  | { kind: "role"; user: User }
  | { kind: "sessions"; user: User }
  | { kind: "reinvite"; user: User }
  | null;

export default function UsersPage() {
  const me = useCurrentUser();
  if (!canManageUsers(me)) {
    return (
      <EmptyState
        tone="forbidden"
        title="Você não tem acesso a esta tela"
        description="Se precisar dela, peça ao administrador do seu escritório."
      />
    );
  }
  return (
    <Suspense>
      <Users />
    </Suspense>
  );
}

function Users() {
  const me = useCurrentUser();
  const { state, update } = useListState();
  const [action, setAction] = useState<Action>(null);
  const [term, setTerm] = useState(state.q);
  const allClients = me.context?.all_clients ?? false;

  // A busca vai para a URL depois de uma pausa na digitação.
  useEffect(() => {
    const timer = setTimeout(() => {
      if (term.trim() !== state.q) update({ q: term.trim() });
    }, 300);
    return () => clearTimeout(timer);
  }, [term, state.q, update]);

  const users = useQuery({
    queryKey: ["users", "list", state, me.context?.client_id ?? "all"],
    queryFn: () => api.get<UserPage>(`/users?${listQuery(state)}`),
    placeholderData: keepPreviousData,
  });

  const columns = useMemo<ColumnDef<User>[]>(() => {
    const base: ColumnDef<User>[] = [
      {
        id: "email",
        header: "E-mail",
        cell: ({ row }) => (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-body text-text">{row.original.email}</span>
            {row.original.is_self && <Badge variant="accent">Você</Badge>}
          </div>
        ),
      },
    ];
    if (allClients) {
      base.push({
        id: "client",
        header: "Cliente",
        enableSorting: false,
        cell: ({ row }) => row.original.client_name,
      });
    }
    base.push(
      {
        id: "role",
        header: "Papel",
        cell: ({ row }) => <Badge>{ROLE_LABEL[row.original.role]}</Badge>,
      },
      {
        id: "mfa",
        header: "Verificação",
        enableSorting: false,
        cell: ({ row }) =>
          row.original.mfa === "active" ? (
            <Badge variant="success-text">MFA ativo</Badge>
          ) : row.original.mfa === "none" ? (
            <Badge variant="warning-text">Sem MFA</Badge>
          ) : (
            <span className="text-text-muted">—</span>
          ),
      },
      {
        id: "last_login_at",
        header: "Último acesso",
        cell: ({ row }) =>
          row.original.last_login_at ? (
            <span className="tabular text-text-secondary">
              {formatListDate(row.original.last_login_at)}
            </span>
          ) : (
            <span className="text-text-muted">—</span>
          ),
      },
      {
        id: "status",
        header: "Situação",
        cell: ({ row }) => (
          <Badge variant={row.original.status === "active" ? "success-text" : "neutral"}>
            {STATUS_LABEL[row.original.status]}
          </Badge>
        ),
      },
    );
    if (!allClients) {
      base.push({
        id: "actions",
        header: () => <span className="sr-only">Ações</span>,
        enableSorting: false,
        cell: ({ row }) => <RowActions user={row.original} onAction={setAction} />,
      });
    }
    return base;
  }, [allClients]);

  const hasFilters = Boolean(state.q);

  return (
    <>
      <PageHeader
        title="Usuários"
        description={
          allClients
            ? "Todos os clientes. Escolha um cliente no topo da barra lateral para convidar ou alterar usuários."
            : `Quem acessa o ${contextName(me)} no Regista e o que cada um pode fazer.`
        }
        actions={
          !allClients && <Button onClick={() => setAction({ kind: "invite" })}>Convidar usuário</Button>
        }
      />

      <div className="relative max-w-[360px]">
        <Search
          aria-hidden
          className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-muted"
        />
        <Input
          aria-label="Buscar e-mail"
          placeholder="Buscar e-mail"
          className="pl-9"
          value={term}
          onChange={(event) => setTerm(event.target.value)}
        />
      </div>

      <DataTable
        columns={columns}
        data={users.data?.items ?? []}
        rowCount={users.data?.total ?? 0}
        state={state}
        onStateChange={update}
        isLoading={users.isPending}
        isFetching={users.isFetching}
        hasFilters={hasFilters}
        getRowId={(row) => row.id}
        emptyState={
          <EmptyState
            title="Nenhum usuário ainda"
            description="Convide a primeira pessoa. Ela recebe um e-mail para criar a senha e ativar a verificação."
            action={
              !allClients ? (
                <Button onClick={() => setAction({ kind: "invite" })}>Convidar usuário</Button>
              ) : undefined
            }
          />
        }
        filteredEmptyState={
          <EmptyState
            tone="filtered"
            title="Nenhum usuário com esses filtros"
            description="Confira o e-mail digitado ou limpe a busca."
            action={
              <Button
                variant="secondary"
                onClick={() => {
                  setTerm("");
                  update({ q: "" });
                }}
              >
                Limpar filtros
              </Button>
            }
          />
        }
        errorState={
          users.isError ? (
            <EmptyState
              tone="error"
              title="Não foi possível carregar os usuários"
              description="O servidor não respondeu. Tente de novo em alguns segundos."
              action={
                <Button variant="secondary" onClick={() => users.refetch()}>
                  Tentar de novo
                </Button>
              }
            />
          ) : undefined
        }
        mobileCard={(user) => (
          <div className="grid gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <p className="break-all text-title text-text">{user.email}</p>
              {user.is_self && <Badge variant="accent">Você</Badge>}
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge>{ROLE_LABEL[user.role]}</Badge>
              <Badge variant={user.status === "active" ? "success-text" : "neutral"}>
                {STATUS_LABEL[user.status]}
              </Badge>
              {allClients && <Badge variant="mono">{user.client_name}</Badge>}
            </div>
            {!allClients && <RowActions user={user} onAction={setAction} stacked />}
          </div>
        )}
      />

      <InviteDialog open={action?.kind === "invite"} onClose={() => setAction(null)} />
      <RoleDialog
        user={action?.kind === "role" ? action.user : null}
        onClose={() => setAction(null)}
      />
      <SessionsDialog
        user={action?.kind === "sessions" ? action.user : null}
        onClose={() => setAction(null)}
      />
      <ReinviteDialog
        user={action?.kind === "reinvite" ? action.user : null}
        onClose={() => setAction(null)}
      />
    </>
  );
}

function RowActions({
  user,
  onAction,
  stacked = false,
}: Readonly<{ user: User; onAction: (action: Action) => void; stacked?: boolean }>) {
  const size = stacked ? "lg" : "default";
  return (
    <div className={stacked ? "grid gap-2" : "flex flex-nowrap items-center justify-end gap-2"}>
      <Button
        variant="secondary"
        size={size}
        disabled={user.is_self}
        onClick={() => onAction({ kind: "role", user })}
      >
        Mudar papel
      </Button>
      <Button
        variant="secondary"
        size={size}
        disabled={user.status === "invited"}
        onClick={() => onAction({ kind: "sessions", user })}
      >
        Encerrar sessões
      </Button>
      {!user.is_self &&
        (stacked ? (
          <Button variant="secondary" size={size} onClick={() => onAction({ kind: "reinvite", user })}>
            Reenviar convite
          </Button>
        ) : (
          // Na tabela, a terceira ação cabe num menu para a linha continuar com 56px.
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="secondary" size="icon" aria-label={`Mais ações para ${user.email}`}>
                <MoreHorizontal aria-hidden className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => onAction({ kind: "reinvite", user })}>
                Reenviar convite
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ))}
    </div>
  );
}

function RolePicker({
  value,
  onChange,
  disabled,
}: Readonly<{ value: Role; onChange: (role: Role) => void; disabled?: boolean }>) {
  return (
    <RadioGroup
      aria-label="Papel"
      value={value}
      onValueChange={(next) => onChange(next as Role)}
      disabled={disabled}
    >
      {ROLES.map((role) => (
        <RadioCard
          key={role.value}
          value={role.value}
          title={role.label}
          description={role.description}
        />
      ))}
    </RadioGroup>
  );
}

function InviteDialog({ open, onClose }: Readonly<{ open: boolean; onClose: () => void }>) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [error, setError] = useState<string | null>(null);

  function close() {
    setEmail("");
    setRole("viewer");
    setError(null);
    onClose();
  }

  const invite = useMutation({
    mutationFn: () =>
      api.post<{ email: string }>("/users/invitations", { email: email.trim(), role }),
    onSuccess: (created) => {
      toast.success(`Convite enviado para ${created.email}.`);
      queryClient.invalidateQueries({ queryKey: ["users"] });
      close();
    },
    onError: (err) => setError(messageFor(err)),
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!EMAIL.test(email.trim())) {
      setError("Falta o domínio. Ex.: pessoa@escritorio-exemplo.com.br");
      return;
    }
    setError(null);
    invite.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && !invite.isPending && close()}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-5" noValidate>
          <DialogHeader>
            <DialogTitle>Convidar usuário</DialogTitle>
            <DialogDescription>
              A pessoa recebe um e-mail para criar a senha e ativar a verificação em duas etapas.
            </DialogDescription>
          </DialogHeader>
          <Field label="E-mail" error={error}>
            {(control) => (
              <Input
                {...control}
                type="email"
                autoFocus
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            )}
          </Field>
          <fieldset className="grid gap-1.5">
            <legend className="mb-1.5 text-body-sm font-medium text-text">Papel</legend>
            <RolePicker value={role} onChange={setRole} />
          </fieldset>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={close} disabled={invite.isPending}>
              Voltar
            </Button>
            <Button type="submit" loading={invite.isPending} loadingText="Enviando…">
              Enviar convite
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RoleDialog({ user, onClose }: Readonly<{ user: User | null; onClose: () => void }>) {
  const queryClient = useQueryClient();
  const [role, setRole] = useState<Role>("viewer");
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Cada vez que o diálogo abre para alguém, começa no papel atual dessa pessoa.
  const [seenId, setSeenId] = useState<string | null>(null);
  if (user && user.id !== seenId) {
    setSeenId(user.id);
    setRole(user.role);
    setError(null);
  }
  if (!user && seenId !== null) setSeenId(null);

  const save = useMutation({
    mutationFn: (target: User) => api.patch(`/users/${target.id}`, { role }),
    onSuccess: () => {
      toast.success("Papel salvo");
      queryClient.invalidateQueries({ queryKey: ["users"] });
      onClose();
    },
    onError: (err) => setError(messageFor(err)),
  });

  const remove = useMutation({
    mutationFn: (target: User) => api.post(`/users/${target.id}/remove-access`),
    onSuccess: () => {
      toast.success("Acesso removido");
      queryClient.invalidateQueries({ queryKey: ["users"] });
      setRemoving(false);
      onClose();
    },
    onError: (err) => setError(messageFor(err)),
  });

  return (
    <>
      <Dialog
        open={user !== null && !removing}
        onOpenChange={(next) => !next && !save.isPending && onClose()}
      >
        <DialogContent>
          <form
            className="grid gap-5"
            onSubmit={(event) => {
              event.preventDefault();
              if (user) {
                setError(null);
                save.mutate(user);
              }
            }}
          >
            <DialogHeader>
              <DialogTitle>Mudar papel</DialogTitle>
              <DialogDescription>{user?.email}</DialogDescription>
            </DialogHeader>
            <RolePicker value={role} onChange={setRole} />
            {error && (
              <p role="alert" className="text-body-sm text-danger-text">
                {error}
              </p>
            )}
            <DialogFooter className="min-[560px]:justify-between">
              <Button
                type="button"
                variant="destructive-outline"
                onClick={() => {
                  setError(null);
                  setRemoving(true);
                }}
              >
                Remover acesso
              </Button>
              <div className="flex flex-col-reverse gap-3 min-[560px]:flex-row">
                <Button type="button" variant="ghost" onClick={onClose} disabled={save.isPending}>
                  Voltar
                </Button>
                <Button type="submit" loading={save.isPending} loadingText="Salvando…">
                  Salvar papel
                </Button>
              </div>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={user !== null && removing}
        onOpenChange={(next) => !next && setRemoving(false)}
        title={`Remover acesso de ${user?.email ?? ""}?`}
        description="A pessoa sai na hora e não consegue mais entrar. Para voltar, será preciso um novo convite."
        confirmLabel="Remover acesso"
        loadingLabel="Removendo…"
        pending={remove.isPending}
        error={error}
        onConfirm={() => user && remove.mutate(user)}
      />
    </>
  );
}

function SessionsDialog({ user, onClose }: Readonly<{ user: User | null; onClose: () => void }>) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const end = useMutation({
    mutationFn: (target: User) => api.post(`/users/${target.id}/revoke-sessions`),
    onSuccess: () => {
      toast.success("Sessões encerradas");
      queryClient.invalidateQueries({ queryKey: ["users"] });
      onClose();
    },
    onError: (err) => setError(messageFor(err)),
  });

  return (
    <ConfirmDialog
      open={user !== null}
      onOpenChange={(next) => {
        if (!next) {
          setError(null);
          onClose();
        }
      }}
      title={`Encerrar sessões de ${user?.email ?? ""}?`}
      description="A pessoa sai do Regista em todos os aparelhos e precisa entrar de novo. Robôs em execução não param."
      confirmLabel="Encerrar sessões"
      loadingLabel="Encerrando…"
      pending={end.isPending}
      error={error}
      onConfirm={() => user && end.mutate(user)}
    />
  );
}

function ReinviteDialog({ user, onClose }: Readonly<{ user: User | null; onClose: () => void }>) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const resend = useMutation({
    mutationFn: (target: User) => api.post(`/users/${target.id}/resend-invitation`),
    onSuccess: () => {
      toast.success("Convite reenviado");
      queryClient.invalidateQueries({ queryKey: ["users"] });
      onClose();
    },
    onError: (err) => setError(messageFor(err)),
  });

  const pending = user?.status === "invited";
  return (
    <ConfirmDialog
      open={user !== null}
      onOpenChange={(next) => {
        if (!next) {
          setError(null);
          onClose();
        }
      }}
      title={`Reenviar convite para ${user?.email ?? ""}?`}
      description={
        pending
          ? "O link anterior deixa de valer e a pessoa recebe um novo e-mail."
          : "A senha e a verificação em duas etapas atuais deixam de valer e a pessoa sai de todos os aparelhos. Ela recebe um e-mail para criar uma nova senha. Robôs em execução não param."
      }
      confirmLabel="Reenviar convite"
      loadingLabel="Reenviando…"
      pending={resend.isPending}
      error={error}
      variant={pending ? "primary" : "destructive"}
      onConfirm={() => user && resend.mutate(user)}
    />
  );
}
