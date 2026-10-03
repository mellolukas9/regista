"use client";

import { Suspense, useMemo, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { DataTable } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { formatSince } from "@/lib/format";
import { messageFor } from "@/lib/messages";
import { useCurrentUser } from "@/lib/me-context";
import { listQuery, useListState } from "@/lib/url-state";

type Client = {
  id: string;
  name: string;
  created_at: string;
  users_count: number;
  machines_total: number;
  machines_online: number;
};
type ClientPage = { items: Client[]; total: number; page: number; per_page: number };

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export default function ClientsPage() {
  const me = useCurrentUser();
  if (!me.is_platform_admin) {
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
      <Clients />
    </Suspense>
  );
}

function Clients() {
  const { state, update } = useListState();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);

  const clients = useQuery({
    queryKey: ["clients", "list", state],
    queryFn: () => api.get<ClientPage>(`/clients?${listQuery(state)}`),
    placeholderData: keepPreviousData,
  });

  const enter = useMutation({
    mutationFn: (clientId: string) => api.put("/auth/context", { client_id: clientId }),
    // "Entrar no cliente": troca o seletor e vai para o início do cliente. Tudo que estava em cache
    // pertence ao contexto anterior, então é invalidado antes de navegar.
    onSuccess: async () => {
      await queryClient.invalidateQueries();
      router.push("/");
    },
    onError: (error) => toast.error(messageFor(error), { duration: Infinity, closeButton: true }),
  });

  const columns = useMemo<ColumnDef<Client>[]>(
    () => [
      {
        id: "name",
        header: "Cliente",
        cell: ({ row }) => (
          <div>
            <p className="text-body text-text">{row.original.name}</p>
            <p className="text-caption text-text-muted">desde {formatSince(row.original.created_at)}</p>
          </div>
        ),
      },
      {
        id: "users",
        header: "Usuários",
        meta: { className: "tabular" },
        cell: ({ row }) => row.original.users_count,
      },
      {
        id: "machines",
        header: "Máquinas",
        enableSorting: false,
        cell: ({ row }) =>
          row.original.machines_total === 0 ? (
            <span className="text-text-muted">—</span>
          ) : (
            <span
              className={`tabular ${row.original.machines_online < row.original.machines_total ? "text-danger-text" : "text-text"}`}
            >
              {row.original.machines_online} de {row.original.machines_total} online
            </span>
          ),
      },
      {
        id: "actions",
        header: () => <span className="sr-only">Ações</span>,
        enableSorting: false,
        meta: { className: "text-right" },
        cell: ({ row }) => (
          <Button
            variant="secondary"
            loading={enter.isPending && enter.variables === row.original.id}
            loadingText="Entrando…"
            onClick={() => enter.mutate(row.original.id)}
          >
            Entrar no cliente
          </Button>
        ),
      },
    ],
    [enter],
  );

  return (
    <>
      <PageHeader
        title="Clientes"
        description="Escritórios e empresas atendidos pela Artemisys no Regista. Só a equipe Artemisys vê esta tela."
        actions={<Button onClick={() => setCreating(true)}>Novo cliente</Button>}
      />
      <DataTable
        columns={columns}
        data={clients.data?.items ?? []}
        rowCount={clients.data?.total ?? 0}
        state={state}
        onStateChange={update}
        isLoading={clients.isPending}
        isFetching={clients.isFetching}
        getRowId={(row) => row.id}
        emptyState={
          <EmptyState
            title="Nenhum cliente ainda"
            description="Cadastre o primeiro cliente. O administrador dele recebe um convite e configura o resto."
            action={<Button onClick={() => setCreating(true)}>Novo cliente</Button>}
          />
        }
        errorState={
          clients.isError ? (
            <EmptyState
              tone="error"
              title="Não foi possível carregar os clientes"
              description="O servidor não respondeu. Tente de novo em alguns segundos."
              action={
                <Button variant="secondary" onClick={() => clients.refetch()}>
                  Tentar de novo
                </Button>
              }
            />
          ) : undefined
        }
        mobileCard={(row) => (
          <div className="grid gap-3">
            <div>
              <p className="text-title text-text">{row.name}</p>
              <p className="text-caption text-text-muted">
                desde {formatSince(row.created_at)} · {row.users_count}{" "}
                {row.users_count === 1 ? "usuário" : "usuários"}
                {row.machines_total > 0 && ` · ${row.machines_online} de ${row.machines_total} online`}
              </p>
            </div>
            <Button
              variant="secondary"
              className="w-full"
              size="lg"
              onClick={() => enter.mutate(row.id)}
            >
              Entrar no cliente
            </Button>
          </div>
        )}
      />
      <NewClientDialog open={creating} onOpenChange={setCreating} />
    </>
  );
}

function NewClientDialog({
  open,
  onOpenChange,
}: Readonly<{ open: boolean; onOpenChange: (open: boolean) => void }>) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState<{ name?: string; email?: string; form?: string }>({});

  function reset(next: boolean) {
    if (!next) {
      setName("");
      setEmail("");
      setErrors({});
    }
    onOpenChange(next);
  }

  const create = useMutation({
    mutationFn: () =>
      api.post<{ id: string; name: string; admin_email: string }>("/clients", {
        name,
        admin_email: email.trim(),
      }),
    onSuccess: (created) => {
      toast.success(`Cliente criado. Convite enviado para ${created.admin_email}.`);
      queryClient.invalidateQueries({ queryKey: ["clients"] });
      reset(false);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "email_in_use") {
        setErrors({ email: messageFor(error) });
      } else if (error instanceof ApiError && error.code === "client_name_taken") {
        setErrors({ name: messageFor(error) });
      } else {
        setErrors({ form: messageFor(error) });
      }
    },
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const next: typeof errors = {};
    if (name.trim().length < 2) next.name = "Escreva o nome do cliente (pelo menos 2 letras).";
    if (!EMAIL.test(email.trim())) {
      next.email = "Falta o domínio. Ex.: admin@escritorio-exemplo.com.br";
    }
    setErrors(next);
    if (!next.name && !next.email) create.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !create.isPending && reset(next)}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-5" noValidate>
          <DialogHeader>
            <DialogTitle>Novo cliente</DialogTitle>
            <DialogDescription>
              O primeiro administrador recebe um convite por e-mail. Depois, ele convida o resto da
              equipe.
            </DialogDescription>
          </DialogHeader>
          <Field
            label="Nome do cliente"
            help="Aparece no seletor de clientes e no topo das telas dele."
            error={errors.name}
          >
            {(control) => (
              <Input
                {...control}
                autoFocus
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            )}
          </Field>
          <Field label="E-mail do primeiro administrador" error={errors.email}>
            {(control) => (
              <Input
                {...control}
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            )}
          </Field>
          {errors.form && (
            <p role="alert" className="text-body-sm text-danger-text">
              {errors.form}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => reset(false)} disabled={create.isPending}>
              Voltar
            </Button>
            <Button type="submit" loading={create.isPending} loadingText="Criando…">
              Criar e enviar convite
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
