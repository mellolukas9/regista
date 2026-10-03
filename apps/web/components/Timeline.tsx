import { Check, X } from "lucide-react";
import { formatListDate } from "@/lib/format";
import { cn } from "@/lib/utils";

export type Step = {
  label: string;
  state: "done" | "current" | "attention" | "failed" | "cancelled" | "future";
  /** Quando a etapa aconteceu (ISO). */
  at?: string | null;
  /** Texto curto ao lado: "há 18 min", "há 2 h · nenhuma máquina livre". */
  note?: string;
};

function Mark({ state }: Readonly<{ state: Step["state"] }>) {
  const base = "relative z-10 grid size-6 shrink-0 place-items-center rounded-full";
  switch (state) {
    case "done":
      return (
        <span className={cn(base, "bg-success text-danger-fg")}>
          <Check aria-hidden className="size-3.5" strokeWidth={3} />
        </span>
      );
    case "current":
      return (
        <span className={cn(base, "border border-running/50 bg-running/15")}>
          <span className="size-2.5 animate-pulse-dot rounded-full bg-running" />
        </span>
      );
    case "attention":
      return (
        <span className={cn(base, "border border-warning/50 bg-warning/15")}>
          <span className="size-2.5 rounded-full bg-warning" />
        </span>
      );
    case "failed":
      return (
        <span className={cn(base, "bg-danger text-danger-fg")}>
          <X aria-hidden className="size-3.5" strokeWidth={3} />
        </span>
      );
    case "cancelled":
      return (
        <span className={cn(base, "border border-neutral/50")}>
          <span className="h-0.5 w-2.5 rounded bg-neutral" />
        </span>
      );
    case "future":
      return <span className={cn(base, "border-2 border-border-strong")} />;
  }
}

const STATE_LABEL: Record<Step["state"], string> = {
  done: "feita",
  current: "em andamento",
  attention: "atenção",
  failed: "falhou",
  cancelled: "cancelada",
  future: "ainda não",
};

/**
 * Timeline de execução (design-system.md §5): quatro etapas fixas (Criada, Na fila, Executando,
 * Finalizada), cada uma com a hora em mono; a linha entre elas é verde até a etapa atual.
 */
export function Timeline({ steps }: Readonly<{ steps: Step[] }>) {
  const lastReached = steps.reduce((found, step, index) => (step.state !== "future" ? index : found), 0);
  return (
    <ol className="grid grid-cols-4 gap-2 max-[640px]:grid-cols-1 max-[640px]:gap-4">
      {steps.map((step, index) => (
        <li key={step.label} className="relative flex items-start gap-3 min-[641px]:flex-col">
          {/* A linha até a próxima etapa (horizontal no desktop, vertical no celular). */}
          {index < steps.length - 1 && (
            <span
              aria-hidden
              className={cn(
                "absolute left-3 top-6 h-[calc(100%+1rem)] w-0.5 max-[640px]:block min-[641px]:left-6 min-[641px]:top-3 min-[641px]:h-0.5 min-[641px]:w-[calc(100%-0.5rem)]",
                index < lastReached ? "bg-success" : "bg-border-strong",
              )}
            />
          )}
          <Mark state={step.state} />
          <div className="min-w-0">
            <p className="text-body font-medium text-text">
              {step.label}
              <span className="sr-only"> ({STATE_LABEL[step.state]})</span>
            </p>
            {step.at && (
              <time dateTime={step.at} className="font-mono text-caption text-text-label">
                {formatListDate(step.at)}
              </time>
            )}
            {step.note && (
              <p
                className={cn(
                  "text-caption",
                  step.state === "attention" ? "text-warning-text" : "text-text-label",
                )}
              >
                {step.note}
              </p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
