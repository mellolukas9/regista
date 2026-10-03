import { statusEntry, TONE_DOT, TONE_TEXT, type StatusKind } from "@/lib/status";
import { cn } from "@/lib/utils";

/**
 * StatusPill (design-system.md §5): 24px, rótulo, tom e ponto vindos de `lib/status.ts`.
 * O ponto é sólido (estável), vazado (aguardando) ou pulsante (acontecendo agora); a forma e o texto
 * dizem o estado, não só a cor.
 */
export function StatusPill({
  kind,
  status,
  className,
}: Readonly<{ kind: StatusKind; status: string; className?: string }>) {
  const { label, tone, dot } = statusEntry(kind, status);
  return (
    <span
      className={cn(
        "inline-flex h-6 w-fit shrink-0 items-center gap-1.5 whitespace-nowrap rounded-pill border border-current/30 bg-current/10 px-2.5 text-caption font-medium",
        TONE_TEXT[tone],
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "size-2 rounded-full border",
          TONE_DOT[tone],
          dot === "hollow" && "bg-transparent",
          dot === "pulse" && "animate-pulse-dot",
        )}
      />
      {label}
    </span>
  );
}
