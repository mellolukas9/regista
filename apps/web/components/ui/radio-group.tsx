"use client";

import * as React from "react";
import { RadioGroup as RadioGroupPrimitive } from "radix-ui";
import { cn } from "@/lib/utils";

function RadioGroup({
  className,
  ...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Root>) {
  return (
    <RadioGroupPrimitive.Root
      data-slot="radio-group"
      className={cn("grid gap-3", className)}
      {...props}
    />
  );
}

/** Radio em card com título e descrição (design-system.md 7.17, escolha de papel). */
function RadioCard({
  className,
  title,
  description,
  ...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Item> & {
  title: string;
  description: string;
}) {
  return (
    <RadioGroupPrimitive.Item
      data-slot="radio-card"
      className={cn(
        "group flex min-h-11 items-start gap-3 rounded-control border border-border-control bg-sidebar p-3 text-left transition-colors hover:border-border-hover disabled:cursor-not-allowed disabled:opacity-45 data-[state=checked]:border-accent-text data-[state=checked]:bg-accent-text/8",
        className,
      )}
      {...props}
    >
      <span
        aria-hidden
        className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border border-border-strong group-data-[state=checked]:border-accent-text"
      >
        <RadioGroupPrimitive.Indicator>
          <span className="block size-2.5 rounded-full bg-accent-text" />
        </RadioGroupPrimitive.Indicator>
      </span>
      <span>
        <span className="block text-body font-medium text-text">{title}</span>
        <span className="mt-0.5 block text-body-sm text-text-label">{description}</span>
      </span>
    </RadioGroupPrimitive.Item>
  );
}

export { RadioGroup, RadioCard };
