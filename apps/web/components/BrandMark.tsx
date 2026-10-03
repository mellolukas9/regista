import { cn } from "@/lib/utils";

/** Marca: quadro accent com "R" + "Regista" + "by Artemisys" (design-system.md §5, Sidebar). */
export function BrandMark({ className }: Readonly<{ className?: string }>) {
  return (
    <span className={cn("flex items-center gap-3", className)}>
      <span
        aria-hidden
        className="grid size-8 place-items-center rounded-control bg-accent text-body-sm font-semibold text-accent-fg"
      >
        R
      </span>
      <span className="leading-tight">
        <span className="block text-title text-text">Regista</span>
        <span className="block text-caption text-text-muted">by Artemisys</span>
      </span>
    </span>
  );
}
