"use client";

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { toast } from "sonner";
import { Banner } from "@/components/Banner";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { KeyReveal } from "@/components/KeyReveal";
import { PageHeader } from "@/components/PageHeader";
import { StatusPill } from "@/components/StatusPill";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { SegmentedControl, SegmentedItem } from "@/components/ui/toggle-group";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import {
  ago,
  lastSignal,
  MACHINE_NAME,
  MODE_LABEL,
  machinesKey,
  poolsKey,
  useAllMachines,
  usePools,
  type IssuedKey,
  type Machine,
  type Pool,
} from "@/lib/machines";
import { messageFor } from "@/lib/messages";
import { canManageMachines } from "@/lib/me";
import { useCurrentUser } from "@/lib/me-context";

const MODE_HELP = {
  service:
    "Serviço: roda em segundo plano, mesmo sem ninguém logado. Use quando o robô não precisa de tela.",
  session:
    "Sessão: roda na sessão do Windows de um usuário logado. Use para sites ou sistemas que exigem tela aberta.",
} as const;

const NAME_FORMAT_ERROR = "Use letras minúsculas, números e hífen. Ex.: estacao-atendimento-03";

export default function MachinesPage() {
  const me = useCurrentUser();
  const queryClient = useQueryClient();
  const allClients = me.context?.all_clients ?? false;
  const contextKey = me.context?.client_id ?? "all";
  const canManage = canManageMachines(me) && !allClients;

  const [showRevoked, setShowRevoked] = useState(false);
  const [dialog, setDialog] = useState<"machine" | "pool" | null>(null);
  const [issued, setIssued] = useState<IssuedKey | null>(null);

  const machines = useAllMachines(showRevoked, contextKey);
  const pools = usePools(contextKey);

  const byPool = useMemo(() => {
    const groups = new Map<string, Machine[]>();
    for (const machine of machines.data?.items ?? []) {
      groups.set(machine.pool_id, [...(groups.get(machine.pool_id) ?? []), machine]);
    }
    return groups;
  }, [machines.data]);

  const silent = (machines.data?.items ?? []).filter((machine) => machine.status === "offline");
  const failed = machines.isError || pools.isError;
  const loading = machines.isPending || pools.isPending;
  const poolList = pools.data ?? [];
  const nothingYet =
    !loading && !failed && poolList.length === 0 && (machines.data?.items.length ?? 0) === 0;
  const revokedCount = machines.data?.revokedCount ?? 0;

  function refresh() {
    queryClient.invalidateQueries({ queryKey: machinesKey });
    queryClient.invalidateQueries({ queryKey: poolsKey });
  }

  return (
    <>
      <PageHeader
        title="Máquinas e pools"
        description="Computadores onde os robôs rodam, com o agente instalado. Um pool reúne máquinas que podem rodar os mesmos robôs."
        actions={
          canManage && (
            <>
              <Button variant="secondary" onClick={() => setDialog("pool")}>
                Novo pool
              </Button>
              <Button onClick={() => setDialog("machine")}>Cadastrar máquina</Button>
            </>
          )
        }
      />

      {allClients && (
        <p className="text-body-sm text-text-label">
          Você está vendo todos os clientes. Para cadastrar máquinas ou criar pools, escolha um
          cliente no topo da barra lateral.
        </p>
      )}

      {silent.map((machine) => (
        <Banner
          key={machine.id}
          tone="danger"
          title={`${machine.name} está sem sinal${machine.last_seen_at ? ` ${ago(machine.last_seen_at)}` : ""}`}
          action={
            <Button asChild variant="secondary">
              <Link href={`/machines/${machine.id}`}>Ver máquina</Link>
            </Button>
          }
        >
          Verifique se o computador está ligado, conectado à internet e se o serviço do agente está
          rodando.
        </Banner>
      ))}

      {loading && (
        <div className="grid gap-4" aria-busy="true">
          <Skeleton className="h-48 w-full rounded-card" />
          <Skeleton className="h-48 w-full rounded-card" />
        </div>
      )}

      {failed && !loading && (
        <EmptyState
          tone="error"
          title="Não foi possível carregar as máquinas"
          description="O servidor não respondeu. As máquinas continuam funcionando; só o painel está sem dados."
          action={
            <Button variant="secondary" onClick={refresh}>
              Tentar de novo
            </Button>
          }
        />
      )}

      {nothingYet && (
        <EmptyState
          title="Nenhuma máquina cadastrada"
          description="Cadastre o computador onde os robôs vão rodar. Você recebe uma chave para instalar o agente nele."
          action={canManage ? <Button onClick={() => setDialog("machine")}>Cadastrar máquina</Button> : undefined}
        />
      )}

      {!loading &&
        !failed &&
        poolList.map((pool) => (
          <PoolCard
            key={pool.id}
            pool={pool}
            machines={byPool.get(pool.id) ?? []}
            showClient={allClients}
          />
        ))}

      {!loading && !failed && (revokedCount > 0 || showRevoked) && (
        <div>
          <Button
            variant="ghost"
            aria-pressed={showRevoked}
            onClick={() => setShowRevoked((current) => !current)}
          >
            {showRevoked ? "Ocultar revogadas" : `Mostrar revogadas (${revokedCount})`}
          </Button>
        </div>
      )}

      <RegisterDialog
        open={dialog === "machine"}
        pools={poolList}
        onClose={() => setDialog(null)}
        onIssued={(key) => {
          setDialog(null);
          setIssued(key);
          refresh();
        }}
      />
      <PoolDialog open={dialog === "pool"} onClose={() => setDialog(null)} />
      <KeyReveal issued={issued} onDone={() => setIssued(null)} />
    </>
  );
}

function PoolCard({
  pool,
  machines,
  showClient,
}: Readonly<{ pool: Pool; machines: Machine[]; showClient: boolean }>) {
  return (
    <Card className="grid gap-4">
      <CardHeader>
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle>{pool.name}</CardTitle>
            {showClient && <Badge variant="mono">{pool.client_name}</Badge>}
          </div>
          <CardDescription>
            {pool.bot_names.length > 0
              ? `Roda: ${pool.bot_names.join(", ")}`
              : "Nenhum bot usa este pool ainda"}
          </CardDescription>
          <p className="mt-0.5 text-caption text-text-label">
            {pool.machines_online} de {pool.machines_total} online
          </p>
        </div>
      </CardHeader>

      {machines.length === 0 ? (
        <p className="text-body-sm text-text-label">Nenhuma máquina neste pool ainda.</p>
      ) : (
        <>
          <div className="max-[899px]:hidden">
            <Table>
              <TableHeader>
                <TableRow className="h-11 hover:bg-transparent">
                  <TableHead>Máquina</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Último sinal</TableHead>
                  <TableHead>Agente</TableHead>
                  <TableHead>Modo</TableHead>
                  <TableHead>Agora</TableHead>
                  <TableHead>
                    <span className="sr-only">Ações</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {machines.map((machine) => (
                  <TableRow key={machine.id}>
                    <TableCell className="font-mono text-body-sm">{machine.name}</TableCell>
                    <TableCell>
                      <StatusPill kind="machine" status={machine.status} />
                    </TableCell>
                    <TableCell className="tabular text-text-secondary">{lastSignal(machine)}</TableCell>
                    <TableCell className="font-mono text-body-sm text-text-secondary">
                      {machine.agent_version ?? "—"}
                    </TableCell>
                    <TableCell className="text-text-secondary">{MODE_LABEL[machine.mode]}</TableCell>
                    <TableCell className="text-body-sm text-text-secondary">
                      {machine.current_job ? (
                        <Link href={`/runs/${machine.current_job.id}`}>
                          Executando {machine.current_job.short_code}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <Link href={`/machines/${machine.id}`} aria-label={`Abrir ${machine.name}`}>
                        Abrir →
                      </Link>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <ul className="grid gap-3 min-[900px]:hidden">
            {machines.map((machine) => (
              <li key={machine.id}>
                <Link
                  href={`/machines/${machine.id}`}
                  className="grid gap-2 rounded-control border border-border-control bg-sidebar p-3 text-text"
                >
                  <span className="font-mono text-title break-all">{machine.name}</span>
                  <StatusPill kind="machine" status={machine.status} />
                  <span className="text-body-sm text-text-secondary">{lastSignal(machine)}</span>
                  <span className="text-caption text-text-label">
                    {MODE_LABEL[machine.mode]}
                    {machine.agent_version ? ` · agente ${machine.agent_version}` : ""}
                  </span>
                  {machine.current_job && (
                    <span className="text-body-sm text-text-secondary">
                      Executando {machine.current_job.short_code}
                    </span>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

function RegisterDialog({
  open,
  pools,
  onClose,
  onIssued,
}: Readonly<{
  open: boolean;
  pools: Pool[];
  onClose: () => void;
  onIssued: (key: IssuedKey) => void;
}>) {
  const [name, setName] = useState("");
  const [poolId, setPoolId] = useState("");
  const [mode, setMode] = useState<"service" | "session">("service");
  const [nameError, setNameError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Sem escolha ainda, começa no primeiro pool (ou no único que existe).
  const chosenPool = poolId || pools[0]?.id || "";

  function reset() {
    setName("");
    setPoolId("");
    setMode("service");
    setNameError(null);
    setError(null);
  }

  function close() {
    reset();
    onClose();
  }

  const register = useMutation({
    mutationFn: () =>
      api.post<IssuedKey>("/machines", { name: name.trim(), pool_id: chosenPool, mode }),
    onSuccess: (key) => {
      reset();
      onIssued(key);
    },
    onError: (err) => {
      if (err instanceof ApiError && err.code === "machine_name_taken") {
        setNameError(messageFor(err));
      } else {
        setError(messageFor(err));
      }
    },
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (!MACHINE_NAME.test(name.trim())) {
      setNameError(NAME_FORMAT_ERROR);
      return;
    }
    setNameError(null);
    register.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && !register.isPending && close()}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-5" noValidate>
          <DialogHeader>
            <DialogTitle>Cadastrar máquina</DialogTitle>
            <DialogDescription>
              Depois de cadastrar, você recebe uma chave para instalar o agente nesse computador.
            </DialogDescription>
          </DialogHeader>

          <Field label="Nome da máquina" error={nameError}>
            {(control) => (
              <Input
                {...control}
                autoFocus
                autoCapitalize="none"
                spellCheck={false}
                className="font-mono"
                placeholder="estacao-atendimento-03"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            )}
          </Field>

          <Field
            label="Pool"
            help={pools.length === 0 ? "Crie um pool antes de cadastrar a máquina." : undefined}
          >
            {(control) => (
              <Select value={chosenPool} onValueChange={setPoolId} disabled={pools.length === 0}>
                <SelectTrigger {...control}>
                  <SelectValue placeholder="Escolha um pool" />
                </SelectTrigger>
                <SelectContent>
                  {pools.map((pool) => (
                    <SelectItem key={pool.id} value={pool.id}>
                      {pool.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </Field>

          <fieldset className="grid gap-1.5">
            <legend className="mb-1.5 text-body-sm font-medium text-text">Como o agente roda</legend>
            <SegmentedControl aria-label="Como o agente roda" value={mode} onValueChange={(next) => setMode(next as "service" | "session")}>
              <SegmentedItem value="service">Serviço</SegmentedItem>
              <SegmentedItem value="session">Sessão</SegmentedItem>
            </SegmentedControl>
            <p className="text-caption text-text-label">{MODE_HELP[mode]}</p>
          </fieldset>

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
              disabled={pools.length === 0}
              loading={register.isPending}
              loadingText="Cadastrando…"
            >
              Cadastrar e gerar chave
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function PoolDialog({ open, onClose }: Readonly<{ open: boolean; onClose: () => void }>) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  function close() {
    setName("");
    setError(null);
    onClose();
  }

  const create = useMutation({
    mutationFn: () => api.post<Pool>("/pools", { name: name.trim() }),
    onSuccess: () => {
      toast.success("Pool criado");
      queryClient.invalidateQueries({ queryKey: poolsKey });
      close();
    },
    onError: (err) => setError(messageFor(err)),
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) {
      setError("Escreva um nome para o pool.");
      return;
    }
    setError(null);
    create.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && !create.isPending && close()}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-5" noValidate>
          <DialogHeader>
            <DialogTitle>Novo pool</DialogTitle>
            <DialogDescription>
              Um pool reúne máquinas que podem rodar os mesmos robôs.
            </DialogDescription>
          </DialogHeader>
          <Field label="Nome do pool" error={error}>
            {(control) => (
              <Input
                {...control}
                autoFocus
                maxLength={80}
                placeholder="Escritório - Atendimento"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            )}
          </Field>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={close} disabled={create.isPending}>
              Voltar
            </Button>
            <Button type="submit" loading={create.isPending} loadingText="Criando…">
              Criar pool
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
