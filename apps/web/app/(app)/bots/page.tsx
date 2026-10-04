"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Bot as BotIcon, Search } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { DataTable } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { PageHeader } from "@/components/PageHeader";
import { RunBars } from "@/components/RunBars";
import { StatusPill } from "@/components/StatusPill";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, ApiError } from "@/lib/api";
import { botsKey, PACKAGE_NAME, type Bot, type BotPage } from "@/lib/bots";
import { formatListDate } from "@/lib/format";
import { POLL_MS, usePools } from "@/lib/machines";
import { canManageBots, canRunJobs } from "@/lib/me";
import { useCurrentUser } from "@/lib/me-context";
import { messageFor } from "@/lib/messages";
import { useRunNow } from "@/lib/run-now";
import { listQuery, useListState } from "@/lib/url-state";

const PACKAGE_FORMAT_ERROR =
  "Use letras minúsculas, números e _, começando por letra. Ex.: demo_busca_wikipedia";

export default function BotsPage() {
  return (
    <Suspense>
      <Bots />
    </Suspense>
  );
}

function Bots() {
  const me = useCurrentUser();
  const { state, update } = useListState();
  const [term, setTerm] = useState(state.q);
  const [registering, setRegistering] = useState(false);
  const allClients = me.context?.all_clients ?? false;
  const contextKey = me.context?.client_id ?? "all";
  const canRun = canRunJobs(me) && !allClients;
  const run = useRunNow();

  useEffect(() => {
    const timer = setTimeout(() => {
      if (term.trim() !== state.q) update({ q: term.trim() });
    }, 300);
    return () => clearTimeout(timer);
  }, [term, state.q, update]);

  const bots = useQuery({
    queryKey: [...botsKey, "list", state, contextKey],
    queryFn: () => api.get<BotPage>(`/bots?${listQuery(state)}`),
    placeholderData: keepPreviousData,
    refetchInterval: POLL_MS,
  });

  const columns = useMemo<ColumnDef<Bot>[]>(() => {
    const base: ColumnDef<Bot>[] = [
      {
        id: "name",
        header: "Bot",
        cell: ({ row }) => (
          <Link href={`/bots/${row.original.id}`} className="flex items-center gap-3 text-text">
            <span
              aria-hidden
              className="grid size-9 shrink-0 place-items-center rounded-control bg-panel-active text-accent-text"
            >
              <BotIcon className="size-[18px]" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-body font-medium">{row.original.name}</span>
              <span className="block truncate font-mono text-caption text-text-label">
                pacote {row.original.package_name}
              </span>
            </span>
          </Link>
        ),
      },
    ];
    if (allClients) {
      base.push({
        id: "client",
        header: "Cliente",
        enableSorting: false,
        cell: ({ row }) => <span className="text-text-secondary">{row.original.client_name}</span>,
      });
    }
    base.push(
      {
        id: "pool",
        header: "Pool",
        cell: ({ row }) => <span className="text-text-secondary">{row.original.pool_name}</span>,
      },
      {
        id: "last_run",
        header: "Última execução",
        enableSorting: false,
        cell: ({ row }) => {
          const last = row.original.last_run;
          if (!last) return <span className="text-text-label">Nunca rodou</span>;
          return (
            <div className="grid gap-1">
              <StatusPill kind="job" status={last.status} />
              <span className="tabular text-caption text-text-label">{formatListDate(last.created_at)}</span>
            </div>
          );
        },
      },
      {
        id: "recent",
        header: "Últimas 10",
        enableSorting: false,
        cell: ({ row }) => <RunBars statuses={row.original.recent_statuses} />,
      },
      {
        id: "actions",
        header: () => <span className="sr-only">Ações</span>,
        enableSorting: false,
        meta: { className: "text-right" },
        cell: ({ row }) => (canRun ? <RunButton bot={row.original} run={run} /> : null),
      },
    );
    return base;
  }, [allClients, canRun, run]);

  const failed = bots.isError && !bots.data;
  const hasFilters = state.q !== "";

  return (
    <>
      <PageHeader
        title="Bots"
        description="Robôs publicados pela Artemisys. Para ver versões, agendamentos e histórico, abra o bot."
        actions={
          canManageBots(me) &&
          !allClients && (
            <Button variant="secondary" onClick={() => setRegistering(true)}>
              Cadastrar bot
            </Button>
          )
        }
      />

      <div className="relative max-w-[360px]">
        <Search
          aria-hidden
          className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-muted"
        />
        <Input
          type="search"
          aria-label="Buscar bot"
          placeholder="Buscar bot"
          className="pl-9"
          value={term}
          onChange={(event) => setTerm(event.target.value)}
        />
      </div>

      <DataTable
        columns={columns}
        data={bots.data?.items ?? []}
        rowCount={bots.data?.total ?? 0}
        state={state}
        onStateChange={update}
        isLoading={bots.isPending}
        isFetching={bots.isFetching}
        hasFilters={hasFilters}
        getRowId={(bot) => bot.id}
        errorState={
          failed ? (
            <EmptyState
              tone="error"
              title="Não foi possível carregar os bots"
              description="O servidor não respondeu. Tente de novo em alguns segundos."
              action={
                <Button variant="secondary" onClick={() => bots.refetch()}>
                  Tentar de novo
                </Button>
              }
            />
          ) : undefined
        }
        emptyState={
          <EmptyState
            title="Nenhum bot cadastrado"
            description="Cadastre o bot e publique a primeira versão. Antes, confira se o cliente já tem uma máquina online no pool."
            action={
              canManageBots(me) && !allClients ? (
                <Button onClick={() => setRegistering(true)}>Cadastrar bot</Button>
              ) : undefined
            }
          />
        }
        filteredEmptyState={
          <EmptyState
            tone="filtered"
            title="Nenhum bot com essa busca"
            description="Confira o nome ou limpe a busca."
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
        mobileCard={(bot) => (
          <div className="grid gap-3">
            <Link href={`/bots/${bot.id}`} className="grid gap-1 text-text">
              <span className="text-title">{bot.name}</span>
              <span className="font-mono text-caption text-text-label">pacote {bot.package_name}</span>
            </Link>
            <div className="flex flex-wrap items-center justify-between gap-2">
              {bot.last_run ? (
                <StatusPill kind="job" status={bot.last_run.status} />
              ) : (
                <span className="text-body-sm text-text-label">Nunca rodou</span>
              )}
              <RunBars statuses={bot.recent_statuses} />
            </div>
            {canRun && <RunButton bot={bot} run={run} full />}
          </div>
        )}
      />

      <RegisterBotDialog open={registering} onClose={() => setRegistering(false)} />
    </>
  );
}

function RunButton({
  bot,
  run,
  full = false,
}: Readonly<{ bot: Bot; run: ReturnType<typeof useRunNow>; full?: boolean }>) {
  const pending = run.isPending && run.variables === bot.id;
  const button = (
    <Button
      loading={pending}
      loadingText="Executando…"
      className={full ? "h-12 w-full" : undefined}
      onClick={() => run.mutate(bot.id)}
    >
      Executar agora
    </Button>
  );
  // Com uma execução ativa o botão continua habilitado: a nova entra na fila (design-system.md 7.5).
  if (!bot.has_active_run) return button;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent>Entra na fila depois da execução atual</TooltipContent>
    </Tooltip>
  );
}

function RegisterBotDialog({ open, onClose }: Readonly<{ open: boolean; onClose: () => void }>) {
  const me = useCurrentUser();
  const queryClient = useQueryClient();
  const pools = usePools(me.context?.client_id ?? "all");
  const [name, setName] = useState("");
  const [packageName, setPackageName] = useState("");
  const [poolId, setPoolId] = useState("");
  const [description, setDescription] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [packageError, setPackageError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const poolList = pools.data ?? [];
  const chosenPool = poolId || poolList[0]?.id || "";

  function close() {
    setName("");
    setPackageName("");
    setPoolId("");
    setDescription("");
    setNameError(null);
    setPackageError(null);
    setError(null);
    onClose();
  }

  const register = useMutation({
    mutationFn: () =>
      api.post<Bot>("/bots", {
        name: name.trim(),
        package_name: packageName.trim(),
        pool_id: chosenPool,
        description: description.trim() || null,
      }),
    onSuccess: () => {
      toast.success("Bot cadastrado");
      queryClient.invalidateQueries({ queryKey: botsKey });
      close();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.code === "bot_name_taken") setNameError(messageFor(err));
      else if (err instanceof ApiError && err.code === "package_name_taken") setPackageError(messageFor(err));
      else setError(messageFor(err));
    },
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    const emptyName = !name.trim();
    const badPackage = !PACKAGE_NAME.test(packageName.trim());
    setNameError(emptyName ? "Escreva um nome para o bot." : null);
    setPackageError(badPackage ? PACKAGE_FORMAT_ERROR : null);
    if (emptyName || badPackage) return;
    register.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && !register.isPending && close()}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-5" noValidate>
          <DialogHeader>
            <DialogTitle>Cadastrar bot</DialogTitle>
            <DialogDescription>
              O bot roda nas máquinas do pool escolhido. A versão assinada é publicada depois.
            </DialogDescription>
          </DialogHeader>

          <Field label="Nome do bot" error={nameError}>
            {(control) => (
              <Input
                {...control}
                autoFocus
                maxLength={120}
                placeholder="Busca na Wikipédia"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            )}
          </Field>

          <Field
            label="Nome do pacote"
            help="É o nome da pasta do robô. Não muda depois de cadastrado."
            error={packageError}
          >
            {(control) => (
              <Input
                {...control}
                autoCapitalize="none"
                spellCheck={false}
                className="font-mono"
                placeholder="demo_busca_wikipedia"
                value={packageName}
                onChange={(event) => setPackageName(event.target.value)}
              />
            )}
          </Field>

          <Field
            label="Pool"
            help={poolList.length === 0 ? "Crie um pool antes de cadastrar o bot." : undefined}
          >
            {(control) => (
              <Select value={chosenPool} onValueChange={setPoolId} disabled={poolList.length === 0}>
                <SelectTrigger {...control}>
                  <SelectValue placeholder="Escolha um pool" />
                </SelectTrigger>
                <SelectContent>
                  {poolList.map((pool) => (
                    <SelectItem key={pool.id} value={pool.id}>
                      {pool.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </Field>

          <Field label="Descrição (opcional)">
            {(control) => (
              <Input
                {...control}
                maxLength={500}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            )}
          </Field>

          {error && (
            <p role="alert" className="text-body-sm text-danger-text">
              {error}
            </p>
          )}

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={close} disabled={register.isPending}>
              Voltar
            </Button>
            <Button
              type="submit"
              disabled={poolList.length === 0}
              loading={register.isPending}
              loadingText="Cadastrando…"
            >
              Cadastrar bot
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
