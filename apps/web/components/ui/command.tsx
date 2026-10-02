"use client";

import * as React from "react";
import { Command as CommandPrimitive } from "cmdk";
import { Check, Search } from "lucide-react";
import { cn } from "@/lib/utils";

// Base do seletor de cliente (design-system.md §5): busca no topo e lista com item selecionado marcado.
function Command({ className, ...props }: React.ComponentProps<typeof CommandPrimitive>) {
  return (
    <CommandPrimitive
      data-slot="command"
      className={cn("flex w-full flex-col overflow-hidden text-text", className)}
      {...props}
    />
  );
}

function CommandInput({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Input>) {
  return (
    <div className="flex h-11 items-center gap-2 border-b border-border-control px-3">
      <Search aria-hidden className="size-4 shrink-0 text-text-label" />
      <CommandPrimitive.Input
        data-slot="command-input"
        className={cn(
          "h-full w-full bg-transparent text-body outline-none placeholder:text-text-muted",
          className,
        )}
        {...props}
      />
    </div>
  );
}

function CommandList({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.List>) {
  return (
    <CommandPrimitive.List
      data-slot="command-list"
      className={cn("max-h-72 overflow-y-auto p-1", className)}
      {...props}
    />
  );
}

function CommandEmpty(props: React.ComponentProps<typeof CommandPrimitive.Empty>) {
  return (
    <CommandPrimitive.Empty
      data-slot="command-empty"
      className="px-3 py-6 text-center text-body-sm text-text-label"
      {...props}
    />
  );
}

function CommandItem({
  className,
  selected = false,
  children,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Item> & { selected?: boolean }) {
  return (
    <CommandPrimitive.Item
      data-slot="command-item"
      className={cn(
        "flex min-h-11 cursor-pointer select-none items-center gap-2 rounded-sm px-3 text-body outline-none data-[disabled=true]:opacity-45 data-[selected=true]:bg-control-hover",
        className,
      )}
      {...props}
    >
      {children}
      {selected && <Check aria-hidden className="ml-auto size-4 text-accent-text" />}
    </CommandPrimitive.Item>
  );
}

export { Command, CommandInput, CommandList, CommandEmpty, CommandItem };
