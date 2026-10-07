import * as React from "react";
import { cn } from "@/lib/utils";

// Mesmo visual do Input (design-system.md §5), com várias linhas.
function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "min-h-24 w-full min-w-0 rounded-control border border-border-control bg-sidebar px-3 py-2.5 text-body text-text outline-none transition-colors placeholder:text-text-muted hover:border-border-hover focus-visible:border-accent-text focus-visible:shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-accent-text)_22%,transparent)] disabled:cursor-not-allowed disabled:opacity-45 aria-invalid:border-danger",
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
