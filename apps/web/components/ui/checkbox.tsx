"use client";

import * as React from "react";
import { Check } from "lucide-react";
import { Checkbox as CheckboxPrimitive } from "radix-ui";
import { cn } from "@/lib/utils";

// design-system.md §5 (Checkbox): 20px dentro de um alvo de 44px; marcado = fundo accent e check branco.
function Checkbox({
  className,
  ...props
}: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        "group inline-grid size-11 shrink-0 place-items-center rounded-control disabled:cursor-not-allowed disabled:opacity-45",
        className,
      )}
      {...props}
    >
      <span
        aria-hidden
        className="grid size-5 place-items-center rounded-sm border border-border-strong bg-sidebar transition-colors group-aria-invalid:border-danger group-data-[state=checked]:border-accent group-data-[state=checked]:bg-accent"
      >
        <CheckboxPrimitive.Indicator>
          <Check className="size-3.5 text-accent-fg" />
        </CheckboxPrimitive.Indicator>
      </span>
    </CheckboxPrimitive.Root>
  );
}

export { Checkbox };
