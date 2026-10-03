"use client";

import * as React from "react";
import { ToggleGroup as ToggleGroupPrimitive } from "radix-ui";
import { cn } from "@/lib/utils";

// SegmentedControl (design-system.md §5): contêiner sidebar com borda de controle, segmentos de 36px
// dentro de um alvo de 44px; ativo = fundo border-control + contorno interno border-hover.
function SegmentedControl({
  className,
  value,
  onValueChange,
  ...props
}: Omit<
  Extract<React.ComponentProps<typeof ToggleGroupPrimitive.Root>, { type: "single" }>,
  "type" | "value" | "defaultValue" | "onValueChange"
> & {
  value: string;
  onValueChange: (value: string) => void;
}) {
  return (
    <ToggleGroupPrimitive.Root
      type="single"
      data-slot="segmented-control"
      value={value}
      // Clicar no segmento ativo não desmarca: sempre há uma escolha.
      onValueChange={(next) => next && onValueChange(next)}
      className={cn(
        "inline-flex min-h-11 w-full items-center gap-1 rounded-control border border-border-control bg-sidebar p-1",
        className,
      )}
      {...props}
    />
  );
}

function SegmentedItem({
  className,
  count,
  children,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive.Item> & { count?: number }) {
  return (
    <ToggleGroupPrimitive.Item
      data-slot="segmented-item"
      className={cn(
        "inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-sm px-3 text-body font-medium text-text-secondary transition-colors hover:text-text disabled:cursor-not-allowed disabled:opacity-45 data-[state=on]:bg-border-control data-[state=on]:text-text data-[state=on]:shadow-[inset_0_0_0_1px_var(--color-border-hover)]",
        className,
      )}
      {...props}
    >
      {children}
      {count !== undefined && <span className="font-mono text-caption text-text-label">{count}</span>}
    </ToggleGroupPrimitive.Item>
  );
}

export { SegmentedControl, SegmentedItem };
