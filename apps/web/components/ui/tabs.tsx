"use client";

import * as React from "react";
import { Tabs as TabsPrimitive } from "radix-ui";
import { cn } from "@/lib/utils";

// Tabs (design-system.md §5): sublinhado de 2px accent-text na aba ativa e contagem opcional.
// Quem usa guarda a aba ativa na URL (`?tab=`).
function Tabs(props: React.ComponentProps<typeof TabsPrimitive.Root>) {
  return <TabsPrimitive.Root data-slot="tabs" {...props} />;
}

function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn("flex gap-6 overflow-x-auto border-b border-border", className)}
      {...props}
    />
  );
}

function TabsTrigger({
  className,
  count,
  children,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger> & { count?: number }) {
  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      className={cn(
        "-mb-px inline-flex h-11 items-center gap-2 border-b-2 border-transparent text-body font-medium text-text-label transition-colors hover:text-text data-[state=active]:border-accent-text data-[state=active]:text-text",
        className,
      )}
      {...props}
    >
      {children}
      {count !== undefined && <span className="font-mono text-caption text-text-muted">{count}</span>}
    </TabsPrimitive.Trigger>
  );
}

function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content data-slot="tabs-content" className={cn("pt-6", className)} {...props} />;
}

export { Tabs, TabsList, TabsTrigger, TabsContent };
