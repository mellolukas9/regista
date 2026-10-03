"use client";

import { useState } from "react";
import { TriangleAlert } from "lucide-react";
import { CopyButton } from "@/components/CopyButton";
import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { formatDateTime } from "@/lib/format";
import type { IssuedKey } from "@/lib/machines";

/**
 * KeyReveal (design-system.md §5): a chave de registro aparece uma vez e a API nunca a devolve de
 * novo. Sem X, sem Esc e sem fechar clicando fora: só "Concluir", liberado depois que a pessoa
 * confirma que guardou a chave. A chave vive só neste estado do React, nunca em storage ou na URL.
 */
export function KeyReveal({
  issued,
  onDone,
}: Readonly<{ issued: IssuedKey | null; onDone: () => void }>) {
  return (
    <Dialog open={issued !== null}>
      {issued && <KeyRevealContent key={issued.enrollment_key} issued={issued} onDone={onDone} />}
    </Dialog>
  );
}

function KeyRevealContent({ issued, onDone }: Readonly<{ issued: IssuedKey; onDone: () => void }>) {
  const [saved, setSaved] = useState(false);
  return (
    <DialogContent
      hideClose
      onEscapeKeyDown={(event) => event.preventDefault()}
      onPointerDownOutside={(event) => event.preventDefault()}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <StatusPill kind="machine" status="pending" />
          <span className="font-mono text-body-sm text-text-secondary">{issued.name}</span>
        </div>
        <DialogTitle>Copie a chave de registro</DialogTitle>
        <DialogDescription>
          Use esta chave no computador para cadastrar o agente. Ela vale uma vez, até{" "}
          {formatDateTime(issued.expires_at)}.
        </DialogDescription>
      </DialogHeader>

      <div
        role="note"
        className="flex items-start gap-3 rounded-control border border-warning/40 bg-warning/10 p-3 text-body-sm text-warning-body"
      >
        <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-warning" />
        <p>
          Esta é a única vez que a chave aparece. Se você fechar sem copiar, será preciso gerar uma
          nova.
        </p>
      </div>

      <div>
        <p className="break-all rounded-control border border-border-control bg-sidebar p-3 font-mono text-body-sm text-text select-all">
          {issued.enrollment_key}
        </p>
        <CopyButton value={issued.enrollment_key} label="Copiar chave" copiedLabel="Copiada" className="mt-3" />
      </div>

      <div className="flex items-center gap-1">
        <Checkbox id="key-saved" checked={saved} onCheckedChange={(next) => setSaved(next === true)} />
        <Label htmlFor="key-saved" className="cursor-pointer">
          Guardei a chave em um lugar seguro
        </Label>
      </div>

      <DialogFooter>
        <Button type="button" disabled={!saved} onClick={onDone}>
          Concluir
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
