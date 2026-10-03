import { CircleAlert, Inbox, Lock, SearchX, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

type Tone = "empty" | "filtered" | "error" | "forbidden";

const DEFAULT_ICON: Record<Tone, LucideIcon> = {
  empty: Inbox,
  filtered: SearchX,
  error: CircleAlert,
  forbidden: Lock,
};

/** EmptyState (design-system.md §5): ícone em quadro de 44px, título, descrição de até 340px e ação. */
export function EmptyState({
  tone = "empty",
  icon,
  title,
  description,
  action,
  className,
}: Readonly<{
  tone?: Tone;
  icon?: LucideIcon;
  title: string;
  description: string;
  action?: React.ReactNode;
  className?: string;
}>) {
  const Icon = icon ?? DEFAULT_ICON[tone];
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-3 rounded-card border border-border bg-panel px-6 py-12 text-center",
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "grid size-11 place-items-center rounded-control bg-panel-active",
          tone === "error" ? "text-danger" : "text-text-label",
        )}
      >
        <Icon className="size-5" />
      </span>
      <div>
        <h2 className="text-title text-text">{title}</h2>
        <p className="mx-auto mt-1 max-w-[340px] text-body-sm text-text-label">{description}</p>
      </div>
      {action}
    </div>
  );
}
