import Link from "next/link";
import { statusEntry, type JobStatus } from "@/lib/status";
import { cn } from "@/lib/utils";

const FILL: Record<string, string> = {
  success: "bg-success",
  danger: "bg-danger",
  neutral: "bg-neutral",
  running: "bg-running",
  warning: "bg-warning",
};

/** "Últimas 10 execuções: 7 concluídas, 2 falharam, 1 cancelada." para quem não vê as cores. */
function summary(statuses: JobStatus[]): string {
  if (statuses.length === 0) return "Nenhuma execução ainda";
  const count = (wanted: JobStatus[]) => statuses.filter((s) => wanted.includes(s)).length;
  const parts = [
    [count(["completed"]), "concluída", "concluídas"],
    [count(["failed"]), "falhou", "falharam"],
    [count(["cancelled"]), "cancelada", "canceladas"],
    [count(["running", "assigned"]), "em andamento", "em andamento"],
    [count(["pending"]), "pendente", "pendentes"],
  ] as const;
  const text = parts
    .filter(([n]) => n > 0)
    .map(([n, one, many]) => `${n} ${n === 1 ? one : many}`)
    .join(", ");
  return `Últimas ${statuses.length} execuções: ${text}`;
}

/**
 * "Últimas 10": uma faixa de barrinhas coloridas pelo status, da mais antiga (esquerda) para a
 * mais nova (design-system.md 7.5 e 7.6). Pequena (10 × 22) na lista de bots; grande (36 × 44) e
 * clicável no detalhe, onde cada barra leva à sua execução.
 */
export function RunBars({
  statuses,
  ids,
  large = false,
}: Readonly<{ statuses: JobStatus[]; ids?: string[]; large?: boolean }>) {
  const size = large ? "h-11 w-9" : "h-[22px] w-2.5";
  return (
    <span
      role="img"
      aria-label={summary(statuses)}
      className={cn("inline-flex items-end", large ? "gap-1.5" : "gap-[3px]")}
    >
      {statuses.length === 0 && <span className="text-body-sm text-text-label">—</span>}
      {statuses.map((status, index) => {
        const { tone, label } = statusEntry("job", status);
        const bar = (
          <span
            key={`${index}-${status}`}
            className={cn("block rounded-[3px]", size, FILL[tone], status === "pending" && "opacity-60")}
          />
        );
        const id = ids?.[index];
        return large && id ? (
          <Link key={id} href={`/runs/${id}`} aria-label={`${label}, abrir execução`} title={label}>
            {bar}
          </Link>
        ) : (
          bar
        );
      })}
    </span>
  );
}
