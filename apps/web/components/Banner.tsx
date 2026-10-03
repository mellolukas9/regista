import { CircleAlert, Info, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";

type Tone = "warning" | "danger" | "info";

const TONES: Record<Tone, { box: string; icon: string; title: string; Icon: typeof Info }> = {
  warning: {
    box: "border-warning/40 bg-warning/10",
    icon: "text-warning",
    title: "text-warning-text",
    Icon: TriangleAlert,
  },
  danger: {
    box: "border-danger/40 bg-danger/10",
    icon: "text-danger",
    title: "text-danger-text",
    Icon: CircleAlert,
  },
  info: {
    box: "border-accent-text/30 bg-accent-text/10",
    icon: "text-accent-text",
    title: "text-accent-text",
    Icon: Info,
  },
};

/** Banner (design-system.md §5): ícone de 20px, título, texto e ação opcional à direita. */
export function Banner({
  tone,
  title,
  children,
  action,
  className,
}: Readonly<{
  tone: Tone;
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}>) {
  const { box, icon, title: titleClass, Icon } = TONES[tone];
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={cn("flex items-start gap-3 rounded-card border p-4", box, className)}
    >
      <Icon aria-hidden className={cn("mt-0.5 size-5 shrink-0", icon)} />
      <div className="min-w-0 flex-1">
        <p className={cn("text-body font-medium", titleClass)}>{title}</p>
        {children && <div className="mt-1 text-body-sm text-text-secondary">{children}</div>}
      </div>
      {action}
    </div>
  );
}
