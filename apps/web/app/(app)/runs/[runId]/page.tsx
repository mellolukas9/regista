"use client";

import { Suspense, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { Banner } from "@/components/Banner";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DetailHeader } from "@/components/DetailHeader";
import { EmptyState } from "@/components/EmptyState";
import { LogViewer } from "@/components/LogViewer";
import { StatusPill } from "@/components/StatusPill";
import { Timeline, type Step } from "@/components/Timeline";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, ApiError } from "@/lib/api";
import { botsKey } from "@/lib/bots";
import { formatDateTimeSeconds } from "@/lib/format";
import {
  duration,
  errorText,
  isActive,
  jobsKey,
  triggerLabel,
  waitingSince,
  type Artifact,
  type JobDetail,
} from "@/lib/jobs";
import { ago, machinesKey, POLL_MS } from "@/lib/machines";
import { canRunJobs } from "@/lib/me";
import { useCurrentUser } from "@/lib/me-context";
import { messageFor } from "@/lib/messages";

const TABS = ["logs", "screenshot"] as const;
type Tab = (typeof TABS)[number];
// Quanto tempo uma execução pode esperar na fila antes de a Timeline chamar atenção.
const STUCK_MS = 10 * 60 * 1000;

export default function RunDetailPage() {
  return (
    <Suspense>
      <RunDetail />
    </Suspense>
  );
}

function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const me = useCurrentUser();
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const queryClient = useQueryClient();
  const allClients = me.context?.all_clients ?? false;
  const contextKey = me.context?.client_id ?? "all";
  const canRun = canRunJobs(me) && !allClients;
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const job = useQuery({
    queryKey: [...jobsKey, "detail", runId, contextKey],
    queryFn: () => api.get<JobDetail>(`/jobs/${runId}`),
    refetchInterval: POLL_MS,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
  });
  const data = job.data;

  const artifacts = useQuery({
    queryKey: [...jobsKey, "artifacts", runId, contextKey, data?.status],
    queryFn: () => api.get<{ items: Artifact[] }>(`/jobs/${runId}/artifacts`).then((r) => r.items),
    enabled: Boolean(data),
    refetchInterval: data && isActive(data.status) ? POLL_MS : false,
  });

  function refresh() {
    queryClient.invalidateQueries({ queryKey: jobsKey });
    queryClient.invalidateQueries({ queryKey: botsKey });
    queryClient.invalidateQueries({ queryKey: machinesKey });
  }

  const cancel = useMutation({
    mutationFn: () => api.post<JobDetail>(`/jobs/${runId}/cancel`),
    onSuccess: (result) => {
      setCancelling(false);
      setError(null);
      toast.success(result.status === "cancelled" ? "Execução cancelada" : "Cancelamento pedido");
      refresh();
    },
    onError: (err) => setError(messageFor(err)),
  });

  const rerun = useMutation({
    mutationFn: () => api.post<JobDetail>(`/jobs/${runId}/rerun`),
    onSuccess: (created) => {
      toast.success("Execução criada", {
        action: { label: "Ver execução", onClick: () => router.push(`/runs/${created.id}`) },
      });
      refresh();
    },
    onError: (err) => toast.error(messageFor(err), { duration: Infinity, closeButton: true }),
  });

  if (job.isPending) {
    return (
      <div className="grid gap-4" aria-busy="true">
        <Skeleton className="h-24 w-full rounded-card" />
        <Skeleton className="h-20 w-full rounded-card" />
        <Skeleton className="h-48 w-full rounded-card" />
      </div>
    );
  }

  if (job.isError || !data) {
    const missing = job.error instanceof ApiError && job.error.status === 404;
    return (
      <EmptyState
        tone="error"
        title={missing ? "Execução não encontrada" : "Não foi possível carregar a execução"}
        description={
          missing
            ? "O link pode estar errado ou a execução já passou do prazo de retenção. Procure pela lista de execuções."
            : "O servidor não respondeu. Tente de novo em alguns segundos."
        }
        action={
          missing ? (
            <Button asChild variant="secondary">
              <Link href="/runs">Voltar para Execuções</Link>
            </Button>
          ) : (
            <Button variant="secondary" onClick={() => job.refetch()}>
              Tentar de novo
            </Button>
          )
        }
      />
    );
  }

  const active = isActive(data.status);
  const screenshots = artifacts.data ?? [];
  const tab: Tab = TABS.find((t) => t === search.get("tab") && (t !== "screenshot" || screenshots.length > 0)) ?? "logs";
  const reason = errorText(data.error_code);
  const started = formatDateTimeSeconds(data.started_at ?? data.created_at);
  const note = [started, duration(data), data.machine_name].filter((part) => part && part !== "—").join(" · ");

  return (
    <>
      <DetailHeader
        backHref="/runs"
        backLabel="Execuções"
        context={`Execução · ${data.short_code}`}
        title={data.bot_name}
        status={<StatusPill kind="job" status={data.status} />}
        note={note}
        actions={
          canRun && (
            <>
              {active && (
                <Button
                  variant="destructive-outline"
                  onClick={() => {
                    setError(null);
                    setCancelling(true);
                  }}
                >
                  Cancelar execução
                </Button>
              )}
              {!active && (
                <Button loading={rerun.isPending} loadingText="Reexecutando…" onClick={() => rerun.mutate()}>
                  Reexecutar
                </Button>
              )}
            </>
          )
        }
        meta={[
          { label: "Gatilho", value: <TriggerValue job={data} /> },
          { label: "Pool", value: data.pool_name },
          { label: "Fila", value: "—" },
          { label: "Lote", value: "—" },
          { label: "Versão do bot", value: "—" },
        ]}
      />

      <section aria-label="Linha do tempo" className="rounded-card border border-border bg-panel p-5">
        <Timeline steps={stepsOf(data)} />
      </section>

      {data.status === "failed" && (
        <Banner tone="danger" title="A execução falhou">
          {reason}
          {data.error_message && (
            <span className="mt-1 block break-words font-mono text-caption text-text-label">
              {data.error_message}
            </span>
          )}
        </Banner>
      )}

      <Tabs
        value={tab}
        onValueChange={(next) => {
          const params = new URLSearchParams(search.toString());
          params.set("tab", next);
          router.replace(`${pathname}?${params}`, { scroll: false });
        }}
      >
        <TabsList aria-label="Seções da execução">
          <TabsTrigger value="logs">Logs</TabsTrigger>
          {screenshots.length > 0 && (
            <TabsTrigger value="screenshot" count={screenshots.length}>
              {data.status === "failed" ? "Captura de erro" : "Captura de tela"}
            </TabsTrigger>
          )}
        </TabsList>
        <TabsContent value="logs" className="pt-4">
          <LogViewer jobId={data.id} active={active} contextKey={contextKey} />
        </TabsContent>
        {screenshots.length > 0 && (
          <TabsContent value="screenshot" className="grid gap-4 pt-4">
            {screenshots.map((shot) => (
              <Screenshot key={shot.id} shot={shot} failed={data.status === "failed"} />
            ))}
          </TabsContent>
        )}
      </Tabs>

      <ConfirmDialog
        open={cancelling}
        onOpenChange={(next) => {
          if (!next) setError(null);
          setCancelling(next);
        }}
        title={`Cancelar ${data.short_code}?`}
        description="O robô para no próximo item. Itens já concluídos continuam concluídos."
        confirmLabel="Cancelar execução"
        loadingLabel="Cancelando…"
        pending={cancel.isPending}
        error={error}
        onConfirm={() => cancel.mutate()}
      />
    </>
  );
}

function TriggerValue({ job }: Readonly<{ job: JobDetail }>) {
  return (
    <span className="grid">
      <span>{triggerLabel(job)}</span>
      {job.triggered_by && <span className="break-all text-caption text-text-label">{job.triggered_by}</span>}
    </span>
  );
}

function Screenshot({ shot, failed }: Readonly<{ shot: Artifact; failed: boolean }>) {
  return (
    <figure className="grid gap-2 rounded-card border border-border bg-panel p-4">
      <figcaption>
        <p className="text-title">{failed ? "Tela no momento do erro" : "Captura de tela"}</p>
        <p className="font-mono text-caption text-text-label">{ago(shot.uploaded_at)}</p>
      </figcaption>
      {/* O endereço passa pela API (sessão e cliente conferidos) e leva a um link de cerca de 1 min:
          nunca há um endereço permanente do arquivo. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={`/api/artifacts/${shot.id}/content`}
        alt={failed ? "Tela do robô no momento do erro" : "Captura de tela feita pelo robô"}
        className="max-h-[70vh] w-full rounded-control border border-border-control object-contain object-top"
      />
      <p className="text-caption text-text-label">
        {failed
          ? "Captura feita pelo robô quando o erro aconteceu. Ela fica guardada pelo prazo de retenção da fila."
          : "Captura feita pelo robô durante a execução. Ela fica guardada pelo prazo de retenção da fila."}
      </p>
    </figure>
  );
}

/** As quatro etapas fixas da Timeline a partir do estado da execução (design-system.md §5). */
function stepsOf(job: JobDetail): Step[] {
  const created: Step = { label: "Criada", state: "done", at: job.created_at };
  const queued = (state: Step["state"], at: string | null, note?: string): Step => ({
    label: "Na fila",
    state,
    at,
    note,
  });
  const running = (state: Step["state"], at: string | null, note?: string): Step => ({
    label: "Executando",
    state,
    at,
    note,
  });
  const finished = (state: Step["state"], label = "Finalizada", at: string | null = null): Step => ({
    label,
    state,
    at,
  });

  switch (job.status) {
    case "pending": {
      const waited = waitingSince(job);
      const stuck = waited > STUCK_MS || !job.pool_has_online_machine;
      const note = stuck
        ? `${ago(job.created_at)} · ${job.pool_has_online_machine ? "esperando a vez" : "nenhuma máquina livre"}`
        : undefined;
      return [created, queued(stuck ? "attention" : "current", null, note), running("future", null), finished("future")];
    }
    case "assigned":
      return [
        created,
        queued("done", job.assigned_at),
        running("current", null, "esperando o robô iniciar"),
        finished("future"),
      ];
    case "running":
      return [
        created,
        queued("done", job.assigned_at),
        running(
          "current",
          job.started_at,
          job.cancel_requested_at ? "cancelamento pedido" : ago(job.started_at ?? job.created_at),
        ),
        finished("future"),
      ];
    case "completed":
      return [
        created,
        queued("done", job.assigned_at),
        running("done", job.started_at),
        finished("done", "Finalizada", job.finished_at),
      ];
    case "failed":
      return [
        created,
        queued("done", job.assigned_at),
        running(job.started_at ? "done" : "future", job.started_at),
        finished("failed", "Finalizada · Falhou", job.finished_at),
      ];
    case "cancelled":
      return [
        created,
        queued(job.assigned_at ? "done" : "cancelled", job.assigned_at),
        running(job.started_at ? "done" : "future", job.started_at),
        finished("cancelled", "Finalizada · Cancelada", job.finished_at),
      ];
  }
}
