"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Field } from "@/components/Field";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";

/**
 * Confirmação de ação destrutiva (design-system.md §5, AlertDialog). "Voltar" é a saída segura e
 * o confirmar repete o verbo do título. O diálogo só fecha quando a ação termina bem; se o servidor
 * recusar, o motivo aparece aqui dentro.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  loadingLabel,
  pending,
  error,
  onConfirm,
  variant = "destructive",
  typeToConfirm,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel: string;
  loadingLabel: string;
  pending: boolean;
  error?: string | null;
  onConfirm: () => void;
  variant?: "destructive" | "primary";
  /** Ação irreversível: o botão só libera quando a pessoa digita este nome ("Digite <nome> para confirmar"). */
  typeToConfirm?: string;
}>) {
  const [typed, setTyped] = useState("");
  const blocked = typeToConfirm !== undefined && typed !== typeToConfirm;
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (pending) return;
        if (!next) setTyped("");
        onOpenChange(next);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader destructive={variant === "destructive"}>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        {typeToConfirm !== undefined && (
          <Field label={`Digite ${typeToConfirm} para confirmar`}>
            {(control) => (
              <Input
                {...control}
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                className="font-mono"
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
              />
            )}
          </Field>
        )}
        {error && (
          <p role="alert" className="text-body-sm text-danger-text">
            {error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Voltar</AlertDialogCancel>
          <AlertDialogAction
            variant={variant}
            disabled={pending || blocked}
            aria-busy={pending || undefined}
            onClick={(event) => {
              event.preventDefault();
              onConfirm();
            }}
          >
            {pending && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
            {pending ? loadingLabel : confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
