"use client";

import { Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { DetailHeader } from "@/components/DetailHeader";
import { EmptyState } from "@/components/EmptyState";
import { RunBars } from "@/components/RunBars";
import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, ApiError } from "@/lib/api";
import { botsKey, successSentence, type BotDetail } from "@/lib/bots";
import { duration, jobsKey, startedLabel, triggerLabel, type JobPage } from "@/lib/jobs";
import { POLL_MS } from "@/lib/machines";
import { canRunJobs } from "@/lib/me";
import { useCurrentUser } from "@/lib/me-context";
import { useRunNow } from "@/lib/run-now";

const TABS = ["overview", "runs"] as const;
type Tab = (typeof TABS)[number];

export default function BotDetailPage() {
  return (
    <Suspense>
      <BotDetailView />
    </Suspense>
  );
}

function BotDetailView() {
  const { botId } = useParams<{ botId: string }>();
  const me = useCurrentUser();
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const run = useRunNow();
  const allClients = me.context?.all_clients ?? false;
  const contextKey = me.context?.client_id ?? "all";
  const canRun = canRunJobs(me) && !allClients;
  const tab: Tab = TABS.find((t) => t === search.get("tab")) ?? "overview";

  const bot = useQuery({
    queryKey: [...botsKey, "detail", botId, contextKey],
    queryFn: () => api.get<BotDetail>(`/bots/${botId}`),
    refetchInterval: POLL_MS,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
  });
  const data = bot.data;

  if (bot.isPending) {
    return (
      <div className="grid gap-4" aria-busy="true">
        <Skeleton className="h-24 w-full rounded-card" />
        <Skeleton className="h-20 w-full rounded-card" />
        <Skeleton className="h-64 w-full rounded-card" />
      </div>
    );
  }

  if (bot.isError || !data) {
    const missing = bot.error instanceof ApiError && bot.error.status === 404;
    return (
      <EmptyState
        tone="error"
        title={missing ? "Bot não encontrado" : "Não foi possível carregar o bot"}
        description={
          missing
            ? "Confira o endereço ou volte para a lista de bots."
            : "O servidor não respondeu. Tente de novo em alguns segundos."
        }
        action={
          missing ? (
            <Button asChild variant="secondary">
              <Link href="/bots">Voltar para Bots</Link>
            </Button>
          ) : (
            <Button variant="secondary" onClick={() => bot.refetch()}>
              Tentar de novo
            </Button>
          )
        }
      />
    );
  }

  const runButton = (
    <Button
      loading={run.isPending}
      loadingText="Criando execução…"
      onClick={() => run.mutate(data.id)}
    >
      Executar agora
    </Button>
  );

  return (
    <>
      <DetailHeader
        backHref="/bots"
        backLabel="Bots"
        context={`Bot · ${data.client_name}`}
        title={data.name}
        status={
          data.last_run ? (
            <StatusPill kind="job" status={data.last_run.status} />
          ) : (
            <span className="text-body-sm text-text-label">Nunca rodou</span>
          )
        }
        note={`pacote ${data.package_name}`}
        actions={
          canRun &&
          (data.has_active_run ? (
            <Tooltip>
              <TooltipTrigger asChild>{runButton}</TooltipTrigger>
              <TooltipContent>Entra na fila depois da execução atual</TooltipContent>
            </Tooltip>
          ) : (
            runButton
          ))
        }
        meta={[
          { label: "Pool", value: data.pool_name },
          {
            label: "Máquinas no pool",
            value: `${data.machines_online} de ${data.machines_total} online`,
          },
          {
            label: "Sucesso · 30 dias",
            value: successSentence(data),
          },
          { label: "Pacote", value: <span className="font-mono">{data.package_name}</span> },
        ]}
      />

      {data.description && <p className="max-w-[680px] text-body text-text-label">{data.description}</p>}

      <Tabs
        value={tab}
        onValueChange={(next) => {
          const params = new URLSearchParams(search.toString());
          params.set("tab", next);
          router.replace(`${pathname}?${params}`, { scroll: false });
        }}
      >
        <TabsList aria-label="Seções do bot">
          <TabsTrigger value="overview">Visão geral</TabsTrigger>
          <TabsTrigger value="runs">Execuções</TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <Overview bot={data} />
        </TabsContent>
        <TabsContent value="runs">
          <RecentRuns botId={data.id} contextKey={contextKey} />
        </TabsContent>
      </Tabs>
    </>
  );
}

function Overview({ bot }: Readonly<{ bot: BotDetail }>) {
  return (
    <section className="grid gap-3 rounded-card border border-border bg-panel p-5">
      <h2 className="text-title">Últimas 10 execuções</h2>
      <RunBars statuses={bot.recent_statuses} ids={bot.recent_ids} large />
      <p className="text-caption text-text-label">
        Mais antiga à esquerda. Verde concluída, vermelho falhou, cinza cancelada, roxo executando.
      </p>
    </section>
  );
}

function RecentRuns({ botId, contextKey }: Readonly<{ botId: string; contextKey: string }>) {
  const runs = useQuery({
    queryKey: [...jobsKey, "of-bot", botId, contextKey],
    queryFn: () => api.get<JobPage>(`/jobs?bot_id=${botId}&per_page=10&period=all`),
    refetchInterval: POLL_MS,
  });

  if (runs.isPending) return <Skeleton className="h-40 w-full rounded-card" />;
  if (runs.isError) {
    return (
      <EmptyState
        tone="error"
        title="Não foi possível carregar as execuções"
        description="O servidor não respondeu. Tente de novo em alguns segundos."
        action={
          <Button variant="secondary" onClick={() => runs.refetch()}>
            Tentar de novo
          </Button>
        }
      />
    );
  }
  if (runs.data.items.length === 0) {
    return (
      <EmptyState
        title="Este bot ainda não rodou"
        description="Use Executar agora para fazer a primeira execução."
      />
    );
  }

  return (
    <div className="grid gap-3">
      <Table>
        <TableHeader>
          <TableRow className="h-11 hover:bg-transparent">
            <TableHead>Execução</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Gatilho</TableHead>
            <TableHead>Início</TableHead>
            <TableHead>Duração</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.data.items.map((job) => (
            <TableRow key={job.id}>
              <TableCell>
                <Link href={`/runs/${job.id}`} className="font-mono text-body-sm">
                  {job.short_code}
                </Link>
              </TableCell>
              <TableCell>
                <StatusPill kind="job" status={job.status} />
              </TableCell>
              <TableCell className="text-text-secondary">{triggerLabel(job)}</TableCell>
              <TableCell className="tabular text-text-secondary">{startedLabel(job)}</TableCell>
              <TableCell className="tabular text-text-secondary">{duration(job)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <div>
        <Link href={`/runs?bot=${botId}`}>Ver todas →</Link>
      </div>
    </div>
  );
}
