import * as React from "react";
import { cn } from "@/lib/utils";

// design-system.md §5 (Input): 44px, fundo sidebar, borda de controle; foco com halo accent-text 22%.
function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "h-11 w-full min-w-0 rounded-control border border-border-control bg-sidebar px-3 text-body text-text outline-none transition-colors placeholder:text-text-muted hover:border-border-hover focus-visible:border-accent-text focus-visible:shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-accent-text)_22%,transparent)] disabled:cursor-not-allowed disabled:opacity-45 aria-invalid:border-danger",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
