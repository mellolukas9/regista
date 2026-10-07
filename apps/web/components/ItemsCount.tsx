import type { Job } from "@/lib/jobs";

/**
 * Coluna Itens (design-system.md 7.3): sucesso em verde, falha e abandono em vermelho. Uma execução
 * que não trabalha com itens (todas, até o M5) mostra "—".
 */
export function ItemsCount({
  job,
}: Readonly<{
  job: Pick<Job, "items_total" | "items_successful" | "items_failed" | "items_abandoned">;
}>) {
  if (job.items_total === 0) return <span className="text-text-label">—</span>;
  const bad = job.items_failed + job.items_abandoned;
  return (
    <span className="tabular">
      <span className="text-success">{job.items_successful}</span>
      <span className="text-text-muted"> · </span>
      <span className={bad > 0 ? "text-danger-text" : "text-text-label"}>{bad}</span>
    </span>
  );
}
