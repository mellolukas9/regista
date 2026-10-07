"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, ApiError } from "@/lib/api";
import { botsKey, type BotDetail } from "@/lib/bots";
import { formatDateTime } from "@/lib/format";
import { messageFor } from "@/lib/messages";
import {
  shortHash,
  useBotVersions,
  versionsKey,
  type BotVersion,
  type UploadTicket,
} from "@/lib/versions";

const NOTE_MAX = 2000;
/** Falha de rede ao enviar o pacote direto ao armazenamento (não passa pela API). */
const UPLOAD_FAILED = "upload_failed";

/**
 * Aba Versões do Detalhe do bot (design-system.md 7.6 e §15). Todos leem; só a equipe Artemisys
 * publica e coloca uma versão em uso (o servidor confere de novo).
 */
export function VersionsTab({
  bot,
  contextKey,
  canManage,
}: Readonly<{ bot: BotDetail; contextKey: string; canManage: boolean }>) {
  const versions = useBotVersions(bot.id, contextKey);
  const [publishing, setPublishing] = useState(false);
  const [activating, setActivating] = useState<BotVersion | null>(null);

  if (versions.isPending) return <Skeleton className="h-40 w-full rounded-card" />;
  if (versions.isError) {
    return (
      <EmptyState
        tone="error"
        title="Não foi possível carregar as versões"
        description="O servidor não respondeu. Tente de novo em alguns segundos."
        action={
          <Button variant="secondary" onClick={() => versions.refetch()}>
            Tentar de novo
          </Button>
        }
      />
    );
  }

  const items = versions.data.items;
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-[680px] text-body-sm text-text-label">
          Cada versão é assinada. O agente só roda o código cujo hash bate com o publicado.
        </p>
        {canManage && items.length > 0 && <Button onClick={() => setPublishing(true)}>Publicar versão</Button>}
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="Nenhuma versão publicada"
          description={
            canManage
              ? "Publique a primeira versão assinada para este bot poder rodar."
              : "A equipe Artemisys ainda não publicou uma versão deste bot."
          }
          action={canManage ? <Button onClick={() => setPublishing(true)}>Publicar versão</Button> : undefined}
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow className="h-11 hover:bg-transparent">
              <TableHead>Versão</TableHead>
              <TableHead>Publicada em</TableHead>
              <TableHead>Por</TableHead>
              <TableHead>Hash</TableHead>
              <TableHead>O que mudou</TableHead>
              {canManage && <TableHead className="w-[1%]" />}
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((version) => (
              <TableRow key={version.id}>
                <TableCell>
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-body-sm">v{version.version}</span>
                    {version.is_current && <Badge variant="accent">Em uso</Badge>}
                  </span>
                </TableCell>
                <TableCell className="tabular text-text-secondary">{formatDateTime(version.published_at)}</TableCell>
                <TableCell className="text-text-secondary">{version.published_by}</TableCell>
                <TableCell>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="font-mono text-caption text-text-secondary">{shortHash(version.package_sha256)}</span>
                    </TooltipTrigger>
                    <TooltipContent className="font-mono">{version.package_sha256}</TooltipContent>
                  </Tooltip>
                </TableCell>
                <TableCell className="max-w-[360px] break-words text-text-secondary">
                  {version.release_note ?? "—"}
                </TableCell>
                {canManage && (
                  <TableCell>
                    {!version.is_current && (
                      <Button variant="secondary" onClick={() => setActivating(version)}>
                        Colocar em uso
                      </Button>
                    )}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <PublishDialog bot={bot} open={publishing} onClose={() => setPublishing(false)} />
      <ActivateDialog bot={bot} version={activating} onClose={() => setActivating(null)} />
    </div>
  );
}

function useRefreshVersions(botId: string) {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: versionsKey(botId) });
    queryClient.invalidateQueries({ queryKey: botsKey });
  };
}

function ActivateDialog({
  bot,
  version,
  onClose,
}: Readonly<{ bot: BotDetail; version: BotVersion | null; onClose: () => void }>) {
  const refresh = useRefreshVersions(bot.id);
  const [error, setError] = useState<string | null>(null);
  const activate = useMutation({
    mutationFn: (target: BotVersion) =>
      api.put<BotVersion>(`/bots/${bot.id}/current-version`, { version_id: target.id }),
    onSuccess: (_, target) => {
      toast.success(`Versão ${target.version} em uso`);
      refresh();
      onClose();
    },
    onError: (err) => setError(messageFor(err)),
  });

  return (
    <ConfirmDialog
      open={version !== null}
      onOpenChange={(open) => {
        if (!open) {
          setError(null);
          onClose();
        }
      }}
      variant="primary"
      title={`Colocar a versão ${version?.version ?? ""} em uso?`}
      description={`A próxima execução de ${bot.name} vai usar esta versão. Execuções em andamento continuam com a versão atual.`}
      confirmLabel="Colocar em uso"
      loadingLabel="Colocar em uso"
      pending={activate.isPending}
      error={error}
      onConfirm={() => version && activate.mutate(version)}
    />
  );
}

type Phase = "idle" | "checking" | "sending";

function PublishDialog({
  bot,
  open,
  onClose,
}: Readonly<{ bot: BotDetail; open: boolean; onClose: () => void }>) {
  const refresh = useRefreshVersions(bot.id);
  const [packageFile, setPackageFile] = useState<File | null>(null);
  const [signatureFile, setSignatureFile] = useState<File | null>(null);
  const [note, setNote] = useState("");
  const [activate, setActivate] = useState(false);
  const [errors, setErrors] = useState<{ pkg?: string; sig?: string; note?: string }>({});
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");

  function close() {
    setPackageFile(null);
    setSignatureFile(null);
    setNote("");
    setActivate(false);
    setErrors({});
    setError(null);
    setPhase("idle");
    onClose();
  }

  async function publish() {
    const found: typeof errors = {};
    if (!packageFile || !packageFile.name.endsWith(".rgpkg")) found.pkg = "Escolha o arquivo do pacote (.rgpkg).";
    if (!signatureFile || !signatureFile.name.endsWith(".rgsig"))
      found.sig = "Escolha o arquivo da assinatura (.rgsig).";
    if (note.length > NOTE_MAX) found.note = "Escreva até 2000 caracteres.";
    setErrors(found);
    setError(null);
    if (found.pkg || found.sig || found.note || !packageFile || !signatureFile) return;

    try {
      setPhase("checking");
      const ticket = await api.post<UploadTicket>(`/bots/${bot.id}/versions/uploads`, {
        signature: await signatureFile.text(),
        release_note: note.trim() || null,
      });
      setPhase("sending");
      let response: Response;
      try {
        // Direto ao armazenamento: a URL pré-assinada é a credencial, então nada de cookie nem token.
        response = await fetch(ticket.upload_url, {
          method: "PUT",
          headers: ticket.upload_headers,
          body: packageFile,
          credentials: "omit",
        });
      } catch {
        throw new ApiError(0, UPLOAD_FAILED, null);
      }
      if (!response.ok) throw new ApiError(response.status, UPLOAD_FAILED, null);
      await api.post<BotVersion>(`/bots/${bot.id}/versions/${ticket.version_id}/complete`, { activate });
      toast.success(`Versão ${ticket.version} publicada`);
      if (activate) toast.success(`Versão ${ticket.version} em uso`);
      refresh();
      close();
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "";
      if (code === "signature_malformed") setErrors({ sig: messageFor(err) });
      else setError(messageFor(err));
      setPhase("idle");
    }
  }

  const busy = phase !== "idle";
  return (
    <Dialog open={open} onOpenChange={(next) => !next && !busy && close()}>
      <DialogContent>
        <form
          className="grid gap-5"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            void publish();
          }}
        >
          <DialogHeader>
            <DialogTitle>Publicar versão</DialogTitle>
            <DialogDescription>
              O pacote e a assinatura saem do regista-pack. A versão é lida da assinatura.
            </DialogDescription>
          </DialogHeader>

          <Field label="Pacote (.rgpkg)" error={errors.pkg}>
            {(control) => (
              <input
                {...control}
                type="file"
                accept=".rgpkg"
                className="text-body-sm text-text-secondary file:mr-3 file:rounded-control file:border file:border-border-control file:bg-panel-active file:px-3 file:py-2 file:text-body-sm file:text-text"
                onChange={(event) => setPackageFile(event.target.files?.[0] ?? null)}
              />
            )}
          </Field>
          <Field label="Assinatura (.rgsig)" error={errors.sig}>
            {(control) => (
              <input
                {...control}
                type="file"
                accept=".rgsig"
                className="text-body-sm text-text-secondary file:mr-3 file:rounded-control file:border file:border-border-control file:bg-panel-active file:px-3 file:py-2 file:text-body-sm file:text-text"
                onChange={(event) => setSignatureFile(event.target.files?.[0] ?? null)}
              />
            )}
          </Field>
          <Field
            label="O que mudou"
            help="Aparece na lista de versões. Até 2000 caracteres."
            error={errors.note}
          >
            {(control) => <Textarea {...control} value={note} onChange={(event) => setNote(event.target.value)} />}
          </Field>
          <label className="flex items-center gap-2 text-body-sm text-text">
            <Checkbox checked={activate} onCheckedChange={(value) => setActivate(value === true)} />
            Colocar em uso assim que for publicada
          </label>

          {error && (
            <p role="alert" className="text-body-sm text-danger-text">
              {error}
            </p>
          )}

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={close} disabled={busy}>
              Voltar
            </Button>
            <Button
              type="submit"
              loading={busy}
              loadingText={phase === "sending" ? "Enviando…" : "Conferindo…"}
            >
              Publicar versão
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
