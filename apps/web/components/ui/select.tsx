"use client";

import * as React from "react";
import { Check, ChevronDown } from "lucide-react";
import { Select as SelectPrimitive } from "radix-ui";
import { cn } from "@/lib/utils";

// Select (design-system.md §5): mesmo visual do Input, chevron-down, lista com shadow-popover e
// o item escolhido com check em accent-text.
const Select = SelectPrimitive.Root;

function SelectTrigger({
  className,
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Trigger>) {
  return (
    <SelectPrimitive.Trigger
      data-slot="select-trigger"
      className={cn(
        "flex h-11 w-full min-w-0 items-center justify-between gap-2 rounded-control border border-border-control bg-sidebar px-3 text-left text-body text-text outline-none transition-colors hover:border-border-hover focus-visible:border-accent-text focus-visible:shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-accent-text)_22%,transparent)] disabled:cursor-not-allowed disabled:opacity-45 aria-invalid:border-danger data-[placeholder]:text-text-muted",
        className,
      )}
      {...props}
    >
      <span className="min-w-0 truncate">{children}</span>
      <SelectPrimitive.Icon asChild>
        <ChevronDown aria-hidden className="size-4 shrink-0 text-text-label" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

const SelectValue = SelectPrimitive.Value;

function SelectContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        data-slot="select-content"
        position="popper"
        sideOffset={4}
        className={cn(
          "z-[60] max-h-72 w-(--radix-select-trigger-width) min-w-40 overflow-y-auto rounded-control border border-border-control bg-panel-active p-1 shadow-popover",
          className,
        )}
        {...props}
      >
        <SelectPrimitive.Viewport>{children}</SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

function SelectItem({
  className,
  children,
  hint,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Item> & { hint?: string }) {
  return (
    <SelectPrimitive.Item
      data-slot="select-item"
      className={cn(
        "relative flex min-h-11 cursor-default select-none items-center justify-between gap-3 rounded-sm px-3 text-body text-text outline-none data-[disabled]:cursor-not-allowed data-[disabled]:opacity-45 data-[highlighted]:bg-control-hover",
        className,
      )}
      {...props}
    >
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
      <span className="flex items-center gap-2">
        {hint && <span className="text-caption text-text-muted">{hint}</span>}
        <SelectPrimitive.ItemIndicator>
          <Check aria-hidden className="size-4 text-accent-text" />
        </SelectPrimitive.ItemIndicator>
      </span>
    </SelectPrimitive.Item>
  );
}

export { Select, SelectTrigger, SelectValue, SelectContent, SelectItem };
