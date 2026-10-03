"use client";

import * as React from "react";
import { TriangleAlert } from "lucide-react";
import { AlertDialog as AlertDialogPrimitive } from "radix-ui";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// design-system.md §5 (AlertDialog): não fecha clicando fora, foco inicial em "Voltar",
// ícone triangle-alert em quadro danger. O texto de sair é sempre "Voltar".
function AlertDialog(props: React.ComponentProps<typeof AlertDialogPrimitive.Root>) {
  return <AlertDialogPrimitive.Root data-slot="alert-dialog" {...props} />;
}

function AlertDialogTrigger(props: React.ComponentProps<typeof AlertDialogPrimitive.Trigger>) {
  return <AlertDialogPrimitive.Trigger data-slot="alert-dialog-trigger" {...props} />;
}

function AlertDialogContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Content>) {
  return (
    <AlertDialogPrimitive.Portal>
      <AlertDialogPrimitive.Overlay className="fixed inset-0 z-50 bg-scrim" />
      <AlertDialogPrimitive.Content
        data-slot="alert-dialog-content"
        className={cn(
          "fixed left-1/2 top-1/2 z-50 grid max-h-[calc(100dvh-32px)] w-[calc(100vw-32px)] max-w-[480px] -translate-x-1/2 -translate-y-1/2 gap-5 overflow-y-auto rounded-card border border-border bg-panel p-6 shadow-overlay",
          className,
        )}
        {...props}
      >
        {children}
      </AlertDialogPrimitive.Content>
    </AlertDialogPrimitive.Portal>
  );
}

function AlertDialogHeader({
  className,
  destructive = true,
  children,
  ...props
}: React.ComponentProps<"div"> & { destructive?: boolean }) {
  return (
    <div data-slot="alert-dialog-header" className={cn("flex gap-4", className)} {...props}>
      {destructive && (
        <span
          aria-hidden
          className="grid size-11 shrink-0 place-items-center rounded-control bg-danger/14 text-danger"
        >
          <TriangleAlert className="size-5" />
        </span>
      )}
      <div className="grid gap-1.5">{children}</div>
    </div>
  );
}

function AlertDialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-dialog-footer"
      className={cn("flex flex-col-reverse gap-3 min-[560px]:flex-row min-[560px]:justify-end", className)}
      {...props}
    />
  );
}

function AlertDialogTitle({
  className,
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Title>) {
  return (
    <AlertDialogPrimitive.Title
      data-slot="alert-dialog-title"
      className={cn("text-title-lg text-text", className)}
      {...props}
    />
  );
}

function AlertDialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Description>) {
  return (
    <AlertDialogPrimitive.Description
      data-slot="alert-dialog-description"
      className={cn("text-body-sm text-text-label", className)}
      {...props}
    />
  );
}

/** "Voltar" (ghost). Recebe o foco inicial, por ser a saída segura. */
function AlertDialogCancel({
  className,
  children = "Voltar",
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Cancel>) {
  return (
    <AlertDialogPrimitive.Cancel
      data-slot="alert-dialog-cancel"
      className={cn(buttonVariants({ variant: "ghost" }), className)}
      {...props}
    >
      {children}
    </AlertDialogPrimitive.Cancel>
  );
}

function AlertDialogAction({
  className,
  variant = "destructive",
  ...props
}: Omit<React.ComponentProps<typeof AlertDialogPrimitive.Action>, "color"> & {
  variant?: "destructive" | "primary";
}) {
  return (
    <AlertDialogPrimitive.Action
      data-slot="alert-dialog-action"
      className={cn(buttonVariants({ variant }), className)}
      {...props}
    />
  );
}

export {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogCancel,
  AlertDialogAction,
};
