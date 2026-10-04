"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Search } from "lucide-react";
import Link from "next/link";
import { DataTable } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { ItemsCount } from "@/components/ItemsCount";
import { PageHeader } from "@/components/PageHeader";
import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SegmentedControl, SegmentedItem } from "@/components/ui/toggle-group";
import { api } from "@/lib/api";
import { useBotOptions } from "@/lib/bots";
import {
  DEFAULT_PERIOD,
  duration,
  jobsKey,
  PERIODS,
  startedLabel,
  triggerLabel,
  type Job,
  type JobPage,
  type Period,
} from "@/lib/jobs";
import { POLL_MS } from "@/lib/machines";
import { canRunJobs } from "@/lib/me";
import { useCurrentUser } from "@/lib/me-context";
import { statusEntry, type JobStatus } from "@/lib/status";
import { listQuery, useFilters, useListState } from "@/lib/url-state";
import { cn } from "@/lib/utils";

const STATUSES: JobStatus[] = ["pending", "assigned", "running", "completed", "failed", "cancelled"];
// Chips da versão mobile (design-system.md 7.3): "Todas" mais os estados que importam no celular.
const CHIPS: { value: string; label: string }[] = [
  { value: "", label: "Todas" },
  { value: "running", label: "Executando" },
  { value: "pending", label: "Pendente" },
  { value: "failed", label: "Falhou" },
  { value: "completed", label: "Concluído" },
  { value: "cancelled", label: "Cancelado" },
];
const ALL = "all";

export default function RunsPage() {
  return (
    <Suspense>
      <Runs />
    </Suspense>
  );
}

function Runs() {
  const me = useCurrentUser();
  const { state, update } = useListState();
  const { values, setFilter } = useFilters(["bot", "status", "period"] as const);
  const [term, setTerm] = useState(state.q);
  const allClients = me.context?.all_clients ?? false;
  const contextKey = me.context?.client_id ?? "all";
  const bots = useBotOptions(contextKey);

  const period = (PERIODS.some((p) => p.value === values.period) ? values.period : DEFAULT_PERIOD) as Period;
  const status = STATUSES.includes(values.status as JobStatus) ? values.status : "";
  const query = {
    ...(values.bot ? { bot_id: values.bot } : {}),
    ...(status ? { status } : {}),
    period,
  };

  useEffect(() => {
    const timer = setTimeout(() => {
      if (term.trim() !== state.q) update({ q: term.trim() });
    }, 300);
    return () => clearTimeout(timer);
  }, [term, state.q, update]);

  const jobs = useQuery({
    queryKey: [...jobsKey, "list", state, query, contextKey],
    queryFn: () => api.get<JobPage>(`/jobs?${listQuery(state, query)}`),
    placeholderData: keepPreviousData,
    refetchInterval: POLL_MS,
  });

  const columns = useMemo<ColumnDef<Job>[]>(() => {
    const base: ColumnDef<Job>[] = [
      {
        id: "bot",
        header: "Execução",
        cell: ({ row }) => (
          <Link href={`/runs/${row.original.id}`} className="grid gap-0.5 text-text">
            <span className="text-body font-medium">{row.original.bot_name}</span>
            <span className="font-mono text-caption text-text-label">{row.original.short_code}</span>
          </Link>
        ),
      },
      {
        id: "status",
        header: "Status",
        cell: ({ row }) => <StatusPill kind="job" status={row.original.status} />,
      },
      {
        id: "trigger",
        header: "Gatilho",
        enableSorting: false,
        cell: ({ row }) => (
          <div className="grid gap-0.5">
            <span className="text-body-sm text-text">{triggerLabel(row.original)}</span>
            {row.original.triggered_by && (
              <span className="truncate text-caption text-text-label">{row.original.triggered_by}</span>
            )}
          </div>
        ),
      },
      {
        id: "machine",
        header: "Máquina",
        enableSorting: false,
        cell: ({ row }) => (
          <span className="font-mono text-body-sm text-text-secondary">
            {row.original.machine_name ?? "—"}
          </span>
        ),
      },
      {
        id: "created_at",
        header: "Início",
        cell: ({ row }) => (
          <span className="tabular text-text-secondary">{startedLabel(row.original)}</span>
        ),
      },
      {
        id: "duration",
        header: "Duração",
        enableSorting: false,
        cell: ({ row }) => <span className="tabular text-text-secondary">{duration(row.original)}</span>,
      },
      {
        id: "items",
        header: "Itens",
        enableSorting: false,
        cell: ({ row }) => <ItemsCount job={row.original} />,
      },
    ];
    if (allClients) {
      base.splice(1, 0, {
        id: "client",
        header: "Cliente",
        enableSorting: false,
        cell: ({ row }) => <span className="text-text-secondary">{row.original.client_name}</span>,
      });
    }
    return base;
  }, [allClients]);

  const total = jobs.data?.total ?? 0;
  const hasFilters = Boolean(state.q || values.bot || status || values.period);
  const failed = jobs.isError && !jobs.data;

  function clearFilters() {
    setTerm("");
    update({ q: "" });
    for (const name of ["bot", "status", "period"]) setFilter(name, "");
  }

  return (
    <>
      <PageHeader
        title="Execuções"
        description="Cada vez que um robô roda. Clique numa execução para ver a linha do tempo, os logs e os itens."
        actions={
          canRunJobs(me) &&
          !allClients && (
            <Button asChild>
              <Link href="/bots">Executar agora</Link>
            </Button>
          )
        }
      />

      <div className="flex flex-wrap items-end gap-3">
        <div className="relative min-w-[240px] flex-1 max-w-[360px]">
          <Search
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-muted"
          />
          <Input
            type="search"
            aria-label="Buscar pelo código"
            placeholder="Buscar pelo código, ex.: exec-7f3a21"
            className="pl-9"
            value={term}
            onChange={(event) => setTerm(event.target.value)}
          />
        </div>

        <Select value={values.bot || ALL} onValueChange={(next) => setFilter("bot", next === ALL ? "" : next)}>
          <SelectTrigger aria-label="Bot" className="w-[200px] max-[899px]:hidden">
            <SelectValue placeholder="Bot: Todos" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Bot: Todos</SelectItem>
            {(bots.data ?? []).map((bot) => (
              <SelectItem key={bot.id} value={bot.id}>
                {bot.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={status || ALL} onValueChange={(next) => setFilter("status", next === ALL ? "" : next)}>
          <SelectTrigger aria-label="Status" className="w-[200px] max-[899px]:hidden">
            <SelectValue placeholder="Status: Todos" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Status: Todos</SelectItem>
            {STATUSES.map((value) => (
              <SelectItem key={value} value={value}>
                {statusEntry("job", value).label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <SegmentedControl
          aria-label="Período"
          value={period}
          onValueChange={(next) => setFilter("period", next === DEFAULT_PERIOD ? "" : next)}
          className="w-auto"
        >
          {PERIODS.map((item) => (
            <SegmentedItem key={item.value} value={item.value}>
              {item.label}
            </SegmentedItem>
          ))}
        </SegmentedControl>

        <p className="ml-auto text-body-sm tabular text-text-label" aria-live="polite">
          {total === 1 ? "1 execução" : `${total} execuções`}
        </p>
      </div>

      {/* Mobile: chips de status roláveis na horizontal. */}
      <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1 min-[900px]:hidden" role="group" aria-label="Status">
        {CHIPS.map((chip) => (
          <button
            key={chip.value || "all"}
            type="button"
            aria-pressed={status === chip.value}
            onClick={() => setFilter("status", chip.value)}
            className={cn(
              "h-11 shrink-0 rounded-pill border px-4 text-body-sm",
              status === chip.value
                ? "border-accent-text bg-accent-text/10 text-accent-text"
                : "border-border-control text-text-secondary",
            )}
          >
            {chip.label}
          </button>
        ))}
      </div>

      <DataTable
        columns={columns}
        data={jobs.data?.items ?? []}
        rowCount={total}
        state={state}
        onStateChange={update}
        isLoading={jobs.isPending}
        isFetching={jobs.isFetching}
        hasFilters={hasFilters}
        getRowId={(job) => job.id}
        errorState={
          failed ? (
            <EmptyState
              tone="error"
              title="Não foi possível carregar as execuções"
              description="O servidor demorou para responder. Seus filtros foram mantidos."
              action={
                <Button variant="secondary" onClick={() => jobs.refetch()}>
                  Tentar de novo
                </Button>
              }
            />
          ) : undefined
        }
        emptyState={
          <EmptyState
            title="Nenhuma execução nos últimos 30 dias"
            description="Dispare um robô agora ou crie um agendamento dentro do bot."
            action={
              <Button asChild variant="secondary">
                <Link href="/bots">Ver bots</Link>
              </Button>
            }
          />
        }
        filteredEmptyState={
          <EmptyState
            tone="filtered"
            title="Nenhuma execução com esses filtros"
            description="Troque o período ou limpe os filtros."
            action={
              <Button variant="secondary" onClick={clearFilters}>
                Limpar filtros
              </Button>
            }
          />
        }
        mobileCard={(job) => (
          <Link href={`/runs/${job.id}`} className="grid gap-2 text-text">
            <span className="flex items-center justify-between gap-2">
              <span className="text-title">{job.bot_name}</span>
              <StatusPill kind="job" status={job.status} />
            </span>
            <span className="font-mono text-caption text-text-label">{job.short_code}</span>
            <span className="text-body-sm text-text-secondary">
              {triggerLabel(job)} · {startedLabel(job)} · {duration(job)}
            </span>
          </Link>
        )}
      />
    </>
  );
}
