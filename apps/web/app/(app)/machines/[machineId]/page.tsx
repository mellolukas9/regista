"use client";

import { useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { Banner } from "@/components/Banner";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DetailHeader } from "@/components/DetailHeader";
import { EmptyState } from "@/components/EmptyState";
import { KeyReveal } from "@/components/KeyReveal";
import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, ApiError } from "@/lib/api";
import { formatDateTime, formatListDate } from "@/lib/format";
import { duration, jobsKey, startedLabel, triggerLabel, type JobPage } from "@/lib/jobs";
import {
  describeEvent,
  lastSignalSentence,
  machinesKey,
  MODE_LABEL,
  poolsKey,
  POLL_MS,
  silenceText,
  type IssuedKey,
  type Machine,
  type MachineDetail,
  type MachineEvent,
} from "@/lib/machines";
import { messageFor } from "@/lib/messages";
import { canManageMachines } from "@/lib/me";
import { useCurrentUser } from "@/lib/me-context";
import { cn } from "@/lib/utils";

type EventPage = { items: MachineEvent[]; total: number; page: number; per_page: number };

const EVENTS_PER_PAGE = 25;

export default function MachineDetailPage() {
  const { machineId } = useParams<{ machineId: string }>();
  const me = useCurrentUser();
  const queryClient = useQueryClient();
  const canManage = canManageMachines(me) && !(me.context?.all_clients ?? false);
  const contextKey = me.context?.client_id ?? "all";

  const [confirmKey, setConfirmKey] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [issued, setIssued] = useState<IssuedKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  const machine = useQuery({
    queryKey: [...machinesKey, "detail", machineId, contextKey],
    queryFn: () => api.get<MachineDetail>(`/machines/${machineId}`),
    refetchInterval: POLL_MS,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
  });
  const data = machine.data;

  // Outra máquina online no mesmo pool muda o texto do banner de "sem sinal".
  const peers = useQuery({
    queryKey: [...machinesKey, "peers", data?.pool_id, contextKey],
    enabled: data?.status === "offline",
    queryFn: () =>
      api.get<{ items: Machine[] }>(`/machines?pool_id=${data?.pool_id}&per_page=50&sort=name`),
  });
  const otherOnline = peers.data?.items.find(
    (peer) => peer.status === "online" && peer.id !== machineId,
  );

  function refresh() {
    queryClient.invalidateQueries({ queryKey: machinesKey });
    queryClient.invalidateQueries({ queryKey: poolsKey });
  }

  const newKey = useMutation({
    mutationFn: () => api.post<IssuedKey>(`/machines/${machineId}/enrollment-key`),
    onSuccess: (key) => {
      setConfirmKey(false);
      setError(null);
      setIssued(key);
      toast.success("Nova chave gerada");
      refresh();
    },
    onError: (err) => {
      if (confirmKey) setError(messageFor(err));
      else toast.error(messageFor(err), { duration: Infinity, closeButton: true });
    },
  });

  const revoke = useMutation({
    mutationFn: (name: string) => api.post(`/machines/${machineId}/revoke`, { confirm_name: name }),
    onSuccess: () => {
      setRevoking(false);
      setError(null);
      toast.success("Máquina revogada");
      refresh();
    },
    onError: (err) => setError(messageFor(err)),
  });

  if (machine.isPending) {
    return (
      <div className="grid gap-4" aria-busy="true">
        <Skeleton className="h-24 w-full rounded-card" />
        <Skeleton className="h-20 w-full rounded-card" />
        <Skeleton className="h-64 w-full rounded-card" />
      </div>
    );
  }

  if (machine.isError || !data) {
    const missing = machine.error instanceof ApiError && machine.error.status === 404;
    return (
      <EmptyState
        tone="error"
        title={missing ? "Máquina não encontrada" : "Não foi possível carregar a máquina"}
        description={
          missing
            ? "Confira o endereço ou volte para a lista de máquinas."
            : "O servidor não respondeu. Tente de novo em alguns segundos."
        }
        action={
          <Button variant="secondary" onClick={() => machine.refetch()}>
            Tentar de novo
          </Button>
        }
      />
    );
  }

  const revoked = data.status === "revoked";
  const allClients = me.context?.all_clients ?? false;
  const system = [data.os_info.system, data.os_info.release].filter(Boolean).join(" ");

  return (
    <>
      <DetailHeader
        backHref="/machines"
        backLabel="Máquinas e pools"
        context={`Máquina · ${data.pool_name}${allClients ? ` · ${data.client_name}` : ""}`}
        title={data.name}
        status={<StatusPill kind="machine" status={data.status} />}
        note={lastSignalSentence(data)}
        actions={
          canManage &&
          !revoked && (
            <>
              <Button
                variant="secondary"
                loading={newKey.isPending && !confirmKey}
                loadingText="Gerando…"
                onClick={() => (data.status === "pending" ? newKey.mutate() : setConfirmKey(true))}
              >
                Gerar nova chave
              </Button>
              <Button
                variant="destructive-outline"
                onClick={() => {
                  setError(null);
                  setRevoking(true);
                }}
              >
                Revogar máquina
              </Button>
            </>
          )
        }
        meta={[
          { label: "Versão do agente", value: <span className="font-mono">{data.agent_version ?? "—"}</span> },
          { label: "Modo", value: MODE_LABEL[data.mode] },
          { label: "Sistema", value: system || "—" },
          { label: "Pool", value: data.pool_name },
          {
            label: revoked ? "Revogada" : "Cadastrada",
            value: revoked && data.revoked_at ? formatDateTime(data.revoked_at) : registered(data),
          },
        ]}
      />

      {data.status === "offline" && (
        <Banner tone="danger" title="O agente parou de responder">
          {silenceText(data.mode, otherOnline?.name)}
        </Banner>
      )}

      <DetailTabs machineId={machineId} contextKey={contextKey} />

      <ConfirmDialog
        open={confirmKey}
        onOpenChange={(next) => {
          if (!next) setError(null);
          setConfirmKey(next);
        }}
        title={`Gerar nova chave para ${data.name}?`}
        description="Quando a nova chave for usada, o agente instalado hoje nessa máquina para de funcionar. Se ela não for usada em 24 h, nada muda."
        confirmLabel="Gerar nova chave"
        loadingLabel="Gerando…"
        variant="primary"
        pending={newKey.isPending}
        error={error}
        onConfirm={() => newKey.mutate()}
      />

      <ConfirmDialog
        open={revoking}
        onOpenChange={(next) => {
          if (!next) setError(null);
          setRevoking(next);
        }}
        title={`Revogar ${data.name}?`}
        description={`A máquina deixa de receber execuções na hora e a chave dela para de funcionar. Para usar de novo, será preciso cadastrar a máquina outra vez.${data.current_job ? " A execução em andamento será cancelada." : ""}`}
        confirmLabel="Revogar máquina"
        loadingLabel="Revogando…"
        typeToConfirm={data.name}
        pending={revoke.isPending}
        error={error}
        onConfirm={() => revoke.mutate(data.name)}
      />

      <KeyReveal issued={issued} onDone={() => setIssued(null)} />
    </>
  );
}

function registered(machine: MachineDetail): string {
  const when = formatDateTime(machine.created_at);
  return machine.created_by ? `${when} · ${machine.created_by}` : when;
}

const TABS = ["history", "runs"] as const;

function DetailTabs({ machineId, contextKey }: Readonly<{ machineId: string; contextKey: string }>) {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const tab = TABS.find((t) => t === search.get("tab")) ?? "history";
  return (
    <Tabs
      value={tab}
      onValueChange={(next) => {
        const params = new URLSearchParams(search.toString());
        params.set("tab", next);
        router.replace(`${pathname}?${params}`, { scroll: false });
      }}
    >
      <TabsList aria-label="Seções da máquina">
        <TabsTrigger value="history">Histórico</TabsTrigger>
        <TabsTrigger value="runs">Execuções</TabsTrigger>
      </TabsList>
      <TabsContent value="history">
        <History machineId={machineId} contextKey={contextKey} />
      </TabsContent>
      <TabsContent value="runs">
        <MachineRuns machineId={machineId} contextKey={contextKey} />
      </TabsContent>
    </Tabs>
  );
}

function MachineRuns({ machineId, contextKey }: Readonly<{ machineId: string; contextKey: string }>) {
  const runs = useQuery({
    queryKey: [...jobsKey, "of-machine", machineId, contextKey],
    queryFn: () => api.get<JobPage>(`/jobs?machine_id=${machineId}&per_page=10&period=all`),
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
        title="Nenhuma execução nesta máquina ainda"
        description="As execuções que ela rodar aparecem aqui."
      />
    );
  }
  return (
    <div className="grid gap-3">
      <Table>
        <TableHeader>
          <TableRow className="h-11 hover:bg-transparent">
            <TableHead>Execução</TableHead>
            <TableHead>Bot</TableHead>
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
              <TableCell className="text-text-secondary">{job.bot_name}</TableCell>
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
    </div>
  );
}

const DOT: Record<ReturnType<typeof describeEvent>["tone"], string> = {
  success: "bg-success",
  danger: "bg-danger",
  neutral: "bg-neutral",
  accent: "bg-accent-text",
};

function History({ machineId, contextKey }: Readonly<{ machineId: string; contextKey: string }>) {
  const events = useInfiniteQuery({
    queryKey: [...machinesKey, "events", machineId, contextKey],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      api.get<EventPage>(
        `/machines/${machineId}/events?page=${pageParam}&per_page=${EVENTS_PER_PAGE}`,
      ),
    getNextPageParam: (last) => (last.page * last.per_page < last.total ? last.page + 1 : undefined),
    refetchInterval: POLL_MS,
  });

  if (events.isPending) return <Skeleton className="h-40 w-full rounded-card" />;
  if (events.isError) {
    return (
      <EmptyState
        tone="error"
        title="Não foi possível carregar o histórico"
        description="O servidor não respondeu. Tente de novo em alguns segundos."
        action={
          <Button variant="secondary" onClick={() => events.refetch()}>
            Tentar de novo
          </Button>
        }
      />
    );
  }

  const items = events.data.pages.flatMap((page) => page.items);
  if (items.length === 0) {
    return (
      <EmptyState
        title="Nenhum evento ainda"
        description="Os eventos aparecem aqui assim que a máquina se cadastrar."
      />
    );
  }

  return (
    <div className="grid gap-4">
      <ol className="grid">
        {items.map((event) => {
          const { title, detail, tone } = describeEvent(event);
          return (
            <li
              key={event.id}
              className="grid grid-cols-[112px_16px_1fr] items-start gap-3 border-b border-border py-3 last:border-b-0 max-[560px]:grid-cols-[16px_1fr]"
            >
              <time
                dateTime={event.created_at}
                className="font-mono text-caption text-text-label max-[560px]:order-3 max-[560px]:col-start-2"
              >
                {formatListDate(event.created_at)}
              </time>
              <span
                aria-hidden
                className={cn("mt-1.5 size-2.5 rounded-full max-[560px]:order-1", DOT[tone])}
              />
              <div className="min-w-0 max-[560px]:order-2">
                <p className="text-body text-text">{title}</p>
                {detail && <p className="text-body-sm text-text-label">{detail}</p>}
              </div>
            </li>
          );
        })}
      </ol>
      {events.hasNextPage && (
        <div>
          <Button
            variant="secondary"
            loading={events.isFetchingNextPage}
            loadingText="Carregando…"
            onClick={() => events.fetchNextPage()}
          >
            Mostrar mais
          </Button>
        </div>
      )}
    </div>
  );
}
