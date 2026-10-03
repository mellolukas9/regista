"use client";

import { useState } from "react";
import Link from "next/link";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Banner } from "@/components/Banner";
import { EmptyState } from "@/components/EmptyState";
import { KPI } from "@/components/KPI";
import { PageHeader } from "@/components/PageHeader";
import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SegmentedControl, SegmentedItem } from "@/components/ui/toggle-group";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { percent, periodLabel, useDashboard, type DashboardData } from "@/lib/dashboard";
import { formatListDate } from "@/lib/format";
import { duration, itemsLabel, PERIODS, startedLabel, type Job, type Period } from "@/lib/jobs";
import { ago } from "@/lib/machines";
import { canRunJobs } from "@/lib/me";
import { contextName, useCurrentUser } from "@/lib/me-context";

export default function DashboardPage() {
  const me = useCurrentUser();
  const [period, setPeriod] = useState<Period>("7d");
  const contextKey = me.context?.client_id ?? "all";
  const dashboard = useDashboard(period, contextKey);
  const allClients = me.context?.all_clients ?? false;

  if (dashboard.isError && !dashboard.data) {
    return (
      <>
        <PageHeader title={allClients ? "Visão geral" : "Dashboard"} description="" />
        <EmptyState
          tone="error"
          title={
            allClients ? "Não foi possível carregar a visão geral" : "Não foi possível carregar o dashboard"
          }
          description="O servidor não respondeu. Seus robôs continuam rodando; só o painel está sem dados agora."
          action={
            <Button variant="secondary" onClick={() => dashboard.refetch()}>
              Tentar de novo
            </Button>
          }
        />
      </>
    );
  }

  const data = dashboard.data;
  if (!data) {
    return (
      <>
        <PageHeader title={allClients ? "Visão geral" : "Dashboard"} description="" />
        <div className="grid grid-cols-4 gap-4 max-[1100px]:grid-cols-2 max-[560px]:grid-cols-1" aria-busy="true">
          {Array.from({ length: 4 }, (_, i) => (
            <KPI key={i} label="​" value="" loading />
          ))}
        </div>
        <Skeleton className="h-64 w-full rounded-card" />
      </>
    );
  }

  return data.all_clients ? (
    <Overview data={data} period={period} onPeriod={setPeriod} />
  ) : (
    <ClientView data={data} clientName={contextName(me)} canRun={canRunJobs(me)} />
  );
}

// --- the Artemisys team, all clients ----------------------------------------------------------

function Overview({
  data,
  period,
  onPeriod,
}: Readonly<{ data: DashboardData; period: Period; onPeriod: (period: Period) => void }>) {
  const stuck = data.attention.filter((item) => item.kind === "job_pending").length;
  const points = data.machines_no_signal + stuck;
  const summary = [
    data.machines_no_signal > 0
      ? `${data.machines_no_signal} ${data.machines_no_signal === 1 ? "máquina sem sinal" : "máquinas sem sinal"}`
      : null,
    stuck > 0 ? `${stuck} ${stuck === 1 ? "execução esperando máquina" : "execuções esperando máquina"}` : null,
  ].filter(Boolean);

  return (
    <>
      <PageHeader
        title="Visão geral"
        description="Todos os clientes. Escolha um cliente no topo da barra lateral para ver só os dados dele."
        actions={
          <SegmentedControl
            aria-label="Período"
            value={period}
            onValueChange={(next) => onPeriod(next as Period)}
            className="w-auto"
          >
            {PERIODS.map((item) => (
              <SegmentedItem key={item.value} value={item.value}>
                {item.label}
              </SegmentedItem>
            ))}
          </SegmentedControl>
        }
      />

      {data.clients_active === 0 ? (
        <EmptyState
          title="Nenhum cliente cadastrado ainda"
          description="Cadastre o primeiro cliente e convide o administrador dele. Os números aparecem aqui assim que houver execuções."
          action={
            <Button asChild>
              <Link href="/clients">Cadastrar cliente</Link>
            </Button>
          }
        />
      ) : (
        <>
          {points > 0 && (
            <Banner tone="warning" title={`${points} ${points === 1 ? "ponto precisa" : "pontos precisam"} de atenção`}>
              {summary.join(" e ")}.
            </Banner>
          )}

          <div className="grid grid-cols-4 gap-4 max-[1100px]:grid-cols-2 max-[560px]:grid-cols-1">
            <KPI label="Clientes ativos" value={data.clients_active ?? "—"} href="/clients" />
            <KPI
              label={`Execuções · ${periodLabel(period)}`}
              value={data.runs_total}
              context={`${data.runs_running} executando · ${data.runs_pending} ${data.runs_pending === 1 ? "pendente" : "pendentes"}`}
            />
            <KPI
              label={`Taxa de sucesso · ${periodLabel(period)}`}
              value={percent(data.success_rate)}
              progress={data.success_rate}
              context={data.runs_finished > 0 ? `${data.runs_finished} execuções terminadas` : "Nenhuma execução terminada"}
            />
            <KPI
              label="Máquinas sem sinal"
              value={data.machines_no_signal}
              tone={data.machines_no_signal > 0 ? "danger" : undefined}
              context={`${data.machines_online} de ${data.machines_total} online`}
              href="/machines"
            />
          </div>

          <div className="grid grid-cols-[2fr_1fr] gap-4 max-[1100px]:grid-cols-1">
            <DailyChart daily={data.daily} />
            <Attention items={data.attention} />
          </div>

          <ByClient rows={data.by_client} period={period} />
          <Latest runs={data.latest_runs} showClient />
        </>
      )}
    </>
  );
}

function ByClient({ rows, period }: Readonly<{ rows: DashboardData["by_client"]; period: Period }>) {
  return (
    <Card className="grid gap-4">
      <CardHeader>
        <CardTitle>Por cliente</CardTitle>
        <Link href="/clients">Ver clientes →</Link>
      </CardHeader>
      <Table>
        <TableHeader>
          <TableRow className="h-11 hover:bg-transparent">
            <TableHead>Cliente</TableHead>
            <TableHead>Execuções · {periodLabel(period)}</TableHead>
            <TableHead>Sucesso</TableHead>
            <TableHead>Máquinas</TableHead>
            <TableHead>Última execução</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.client_id}>
              <TableCell className="text-text">{row.client_name}</TableCell>
              <TableCell className="tabular">{row.runs}</TableCell>
              <TableCell className="tabular">{percent(row.success_rate)}</TableCell>
              <TableCell>
                <span className="tabular text-text-secondary">
                  {row.machines_online} de {row.machines_total} online
                </span>
                {row.machines_no_signal > 0 && (
                  <span className="ml-2 text-caption text-danger-text">{row.machines_no_signal} sem sinal</span>
                )}
              </TableCell>
              <TableCell className="tabular text-text-secondary">
                {row.last_run_at ? formatListDate(row.last_run_at) : "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

// --- a client ---------------------------------------------------------------------------------

function ClientView({
  data,
  clientName,
  canRun,
}: Readonly<{ data: DashboardData; clientName: string; canRun: boolean }>) {
  const empty = data.latest_runs.length === 0;
  const worst = data.silent[0];

  return (
    <>
      <PageHeader
        title="Dashboard"
        description={`Como estão as automações do ${clientName} agora.`}
        actions={
          canRun && (
            <Button asChild>
              <Link href="/bots">Executar agora</Link>
            </Button>
          )
        }
      />

      {worst && (
        <Banner
          tone="danger"
          title={`${worst.name} está sem sinal${worst.last_seen_at ? ` desde ${formatListDate(worst.last_seen_at).slice(-5)}` : ""}`}
          action={
            <Button asChild variant="secondary">
              <Link href={`/machines/${worst.id}`}>Ver máquina</Link>
            </Button>
          }
        >
          {worst.other_online
            ? `As execuções seguem pela ${worst.other_online}, uma de cada vez. `
            : "Até o agente voltar, as execuções do pool ficam pendentes. "}
          Verifique se o computador está ligado e conectado à internet.
        </Banner>
      )}

      {empty && data.machines.length === 0 ? (
        <EmptyState
          title="Ainda não há execuções"
          description="Quando a Artemisys publicar seu primeiro robô, ele aparece em Bots. Antes disso, cadastre a máquina onde ele vai rodar."
          action={
            <Button asChild>
              <Link href="/machines">Cadastrar máquina</Link>
            </Button>
          }
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 max-[560px]:grid-cols-1">
            <KPI
              label="Execuções · 7 dias"
              value={data.runs_total}
              context={`${data.runs_running} executando · ${data.runs_pending} ${data.runs_pending === 1 ? "pendente" : "pendentes"}`}
              href="/runs"
            />
            <KPI
              label="Máquinas online"
              value={data.machines_online}
              context={`${data.machines_online} de ${data.machines_total}${data.machines_no_signal > 0 ? ` · ${data.machines_no_signal} sem sinal` : ""}`}
              tone={data.machines_no_signal > 0 ? "danger" : undefined}
              href="/machines"
            />
          </div>

          <div className="grid grid-cols-2 gap-4 max-[1100px]:grid-cols-1">
            <MachinesCard machines={data.machines} />
            <StuckCard runs={data.pending_runs} />
          </div>

          <Latest runs={data.latest_runs} />
        </>
      )}
    </>
  );
}

function MachinesCard({ machines }: Readonly<{ machines: DashboardData["machines"] }>) {
  return (
    <Card className="grid content-start gap-3">
      <CardHeader>
        <CardTitle>Máquinas</CardTitle>
        <Link href="/machines">Ver máquinas →</Link>
      </CardHeader>
      {machines.length === 0 ? (
        <p className="text-body-sm text-text-label">Nenhuma máquina cadastrada ainda.</p>
      ) : (
        <ul className="grid">
          {machines.map((machine) => (
            <li
              key={machine.id}
              className="flex items-center justify-between gap-3 border-b border-border py-3 last:border-b-0"
            >
              <Link href={`/machines/${machine.id}`} className="min-w-0 text-text">
                <span className="block truncate font-mono text-body-sm">{machine.name}</span>
                <span className="block text-caption text-text-label">
                  {machine.current_job
                    ? `Executando ${machine.current_job.short_code}`
                    : machine.last_seen_at
                      ? `Último sinal ${ago(machine.last_seen_at)}`
                      : "Ainda sem sinal"}
                </span>
              </Link>
              <StatusPill kind="machine" status={machine.status} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function StuckCard({ runs }: Readonly<{ runs: Job[] }>) {
  return (
    <Card className="grid content-start gap-3">
      <CardHeader>
        <CardTitle>Parados</CardTitle>
      </CardHeader>
      {runs.length === 0 ? (
        <p className="text-body-sm text-text-label">Nada parado agora.</p>
      ) : (
        <ul className="grid">
          {runs.map((run) => (
            <li
              key={run.id}
              className="flex items-center justify-between gap-3 border-b border-border py-3 last:border-b-0"
            >
              <Link href={`/runs/${run.id}`} className="min-w-0 text-text">
                <span className="block truncate text-body-sm">{run.bot_name}</span>
                <span className="block font-mono text-caption text-text-label">
                  {run.short_code} · {ago(run.created_at)}
                </span>
              </Link>
              <StatusPill kind="job" status={run.status} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// --- shared pieces ----------------------------------------------------------------------------

function DailyChart({ daily }: Readonly<{ daily: DashboardData["daily"] }>) {
  const rows = daily.map((d) => ({
    label: `${d.day.slice(8, 10)}/${d.day.slice(5, 7)}`,
    Concluído: d.completed,
    Falhou: d.failed,
  }));
  const none = daily.every((d) => d.completed + d.failed === 0);
  return (
    <Card className="grid gap-3">
      <CardHeader>
        <CardTitle>Execuções por dia · 14 dias</CardTitle>
      </CardHeader>
      {none ? (
        <p className="py-10 text-center text-body-sm text-text-label">
          Nenhuma execução terminada nos últimos 14 dias.
        </p>
      ) : (
        <div
          role="img"
          aria-label={`Execuções por dia nos últimos 14 dias: ${daily.reduce((n, d) => n + d.completed, 0)} concluídas e ${daily.reduce((n, d) => n + d.failed, 0)} com falha`}
          className="h-[240px]"
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--color-border)" />
              <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: "var(--color-text-label)", fontSize: 12 }} />
              <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: "var(--color-text-label)", fontSize: 12 }} />
              <Tooltip
                cursor={{ fill: "var(--color-panel-active)" }}
                contentStyle={{
                  background: "var(--color-panel)",
                  border: "1px solid var(--color-border-control)",
                  borderRadius: 8,
                  color: "var(--color-text)",
                }}
              />
              <Bar dataKey="Concluído" stackId="runs" fill="var(--color-success)" />
              <Bar dataKey="Falhou" stackId="runs" fill="var(--color-danger-chart)" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      <p className="flex gap-4 text-caption text-text-label">
        <span className="flex items-center gap-1.5">
          <span aria-hidden className="size-2.5 rounded-sm bg-success" /> Concluído
        </span>
        <span className="flex items-center gap-1.5">
          <span aria-hidden className="size-2.5 rounded-sm bg-danger-chart" /> Falhou
        </span>
      </p>
    </Card>
  );
}

function Attention({ items }: Readonly<{ items: DashboardData["attention"] }>) {
  return (
    <Card className="grid content-start gap-3">
      <CardHeader>
        <CardTitle>Precisa de atenção</CardTitle>
      </CardHeader>
      {items.length === 0 ? (
        <p className="text-body-sm text-text-label">Nada precisa de atenção agora.</p>
      ) : (
        <ul className="grid">
          {items.map((item) => (
            <li key={`${item.kind}-${item.id}`} className="border-b border-border py-3 last:border-b-0">
              <Link
                href={item.kind === "machine_offline" ? `/machines/${item.id}` : `/runs/${item.id}`}
                className="grid gap-1 text-text"
              >
                <StatusPill kind={item.kind === "machine_offline" ? "machine" : "job"} status={item.kind === "machine_offline" ? "offline" : "pending"} />
                <span className="font-mono text-body-sm">{item.label}</span>
                <span className="text-caption text-text-label">
                  {item.client_name}
                  {item.since ? ` · ${ago(item.since)}` : ""}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Latest({ runs, showClient = false }: Readonly<{ runs: Job[]; showClient?: boolean }>) {
  return (
    <Card className="grid gap-4">
      <CardHeader>
        <CardTitle>Últimas execuções</CardTitle>
        <Link href="/runs">Ver todas →</Link>
      </CardHeader>
      {runs.length === 0 ? (
        <p className="text-body-sm text-text-label">Nenhuma execução ainda.</p>
      ) : (
        <>
          <div className="max-[899px]:hidden">
            <Table>
              <TableHeader>
                <TableRow className="h-11 hover:bg-transparent">
                  <TableHead>Execução</TableHead>
                  {showClient && <TableHead>Cliente</TableHead>}
                  <TableHead>Status</TableHead>
                  <TableHead>Início</TableHead>
                  <TableHead>Duração</TableHead>
                  {!showClient && <TableHead>Itens</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((run) => {
                  const { ok, bad } = itemsLabel(run);
                  return (
                    <TableRow key={run.id}>
                      <TableCell>
                        <Link href={`/runs/${run.id}`} className="grid gap-0.5 text-text">
                          <span className="text-body font-medium">{run.bot_name}</span>
                          <span className="font-mono text-caption text-text-label">{run.short_code}</span>
                        </Link>
                      </TableCell>
                      {showClient && <TableCell className="text-text-secondary">{run.client_name}</TableCell>}
                      <TableCell>
                        <StatusPill kind="job" status={run.status} />
                      </TableCell>
                      <TableCell className="tabular text-text-secondary">{startedLabel(run)}</TableCell>
                      <TableCell className="tabular text-text-secondary">{duration(run)}</TableCell>
                      {!showClient && (
                        <TableCell className="tabular">
                          <span className="text-success">{ok}</span>
                          <span className="text-text-muted"> · </span>
                          <span className={bad > 0 ? "text-danger-text" : "text-text-label"}>{bad}</span>
                        </TableCell>
                      )}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          <ul className="grid gap-3 min-[900px]:hidden">
            {runs.map((run) => (
              <li key={run.id}>
                <Link
                  href={`/runs/${run.id}`}
                  className="grid gap-1.5 rounded-control border border-border-control bg-sidebar p-3 text-text"
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="text-title">{run.bot_name}</span>
                    <StatusPill kind="job" status={run.status} />
                  </span>
                  <span className="font-mono text-caption text-text-label">{run.short_code}</span>
                  <span className="text-body-sm text-text-secondary">
                    {startedLabel(run)} · {duration(run)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}
