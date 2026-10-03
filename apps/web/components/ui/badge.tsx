import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

// design-system.md §5 (Badge): raio 6px, 22px de altura. Papel, versão, hash, "MFA ativo", "Você"...
const badgeVariants = cva(
  "inline-flex h-[22px] w-fit shrink-0 items-center gap-1 whitespace-nowrap rounded-sm border px-2 text-caption font-medium",
  {
    variants: {
      variant: {
        neutral: "border-border-control bg-panel-active text-text-secondary",
        accent: "border-accent-text/30 bg-accent-text/10 text-accent-text",
        mono: "border-border-control bg-panel-active font-mono text-text-secondary",
        "success-text": "border-success/40 bg-success/14 text-success",
        "warning-text": "border-warning/40 bg-warning/14 text-warning-text",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

function Badge({
  className,
  variant,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return (
    <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { Badge, badgeVariants };
