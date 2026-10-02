import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2, type LucideIcon } from "lucide-react";
import { Slot } from "radix-ui";
import { cn } from "@/lib/utils";

// design-system.md §5 (Button): altura 44px, sem tamanho "sm", destructive só dentro de AlertDialog.
const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-control text-body font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45 [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-accent text-accent-fg hover:bg-accent-hover",
        secondary:
          "border border-border-control bg-panel-active text-text hover:bg-control-hover",
        ghost: "text-text-secondary hover:bg-panel-active hover:text-text",
        destructive: "bg-danger text-danger-fg hover:bg-danger-hover",
        "destructive-outline":
          "border border-danger/40 bg-panel-active text-danger hover:bg-control-hover",
      },
      size: {
        default: "h-11 px-4",
        lg: "h-12 px-5",
        icon: "size-11",
      },
    },
    defaultVariants: { variant: "primary", size: "default" },
  },
);

type ButtonProps = React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
    loading?: boolean;
    /** Verbo no gerúndio exibido durante o carregamento, ex.: "Reprocessando…". */
    loadingText?: string;
    icon?: LucideIcon;
  };

function Button({
  className,
  variant,
  size,
  asChild = false,
  loading = false,
  loadingText,
  icon: Icon,
  children,
  disabled,
  ...props
}: ButtonProps) {
  const classes = cn(buttonVariants({ variant, size }), className);
  if (asChild) {
    return (
      <Slot.Root data-slot="button" className={classes} {...props}>
        {children}
      </Slot.Root>
    );
  }
  return (
    <button
      data-slot="button"
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? (
        <Loader2 aria-hidden className="size-3.5 animate-spin" />
      ) : (
        Icon && <Icon aria-hidden className="size-4" />
      )}
      {loading ? (loadingText ?? children) : children}
    </button>
  );
}

export { Button, buttonVariants };
