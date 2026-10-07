import Link from "next/link";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * KPI (design-system.md §5): rótulo, valor de 36px tabular, contexto e barra opcional (0–1).
 * `tone="danger"` muda a borda e o rótulo; com `href` o card inteiro é um link.
 */
export function KPI({
  label,
  value,
  context,
  progress,
  tone,
  href,
  loading = false,
  error = false,
  onRetry,
}: Readonly<{
  label: string;
  value: React.ReactNode;
  context?: React.ReactNode;
  progress?: number | null;
  tone?: "danger";
  href?: string;
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
}>) {
  const body = (
    <>
      {/* Classe montada à mão: o tailwind-merge confundiria text-overline (tamanho) com cor. */}
      <p className={`text-overline uppercase ${tone === "danger" ? "text-danger-text" : "text-text-muted"}`}>
        {label}
      </p>
      {loading ? (
        <Skeleton className="mt-2 h-9 w-24" />
      ) : error ? (
        <>
          <p className="mt-1 text-[36px] leading-tight tabular text-text-muted">—</p>
          <p className="text-caption text-text-label">
            Não foi possível carregar.{" "}
            {onRetry && (
              <button type="button" className="text-accent-text underline" onClick={onRetry}>
                Tentar de novo
              </button>
            )}
          </p>
        </>
      ) : (
        <>
          <p className="mt-1 text-[36px] font-medium leading-tight tabular text-text">{value}</p>
          {progress !== undefined && progress !== null && (
            <div
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(progress * 100)}
              aria-label={label}
              className="mt-2 h-1.5 overflow-hidden rounded-pill bg-border-control"
            >
              <div
                className="h-full rounded-pill bg-success"
                style={{ width: `${Math.round(progress * 100)}%` }}
              />
            </div>
          )}
          {context && <p className="mt-1 text-caption text-text-label">{context}</p>}
        </>
      )}
    </>
  );

  const box = cn(
    "block rounded-card border bg-panel p-5",
    tone === "danger" ? "border-danger/40" : "border-border",
  );
  if (href && !loading && !error) {
    return (
      <Link href={href} className={cn(box, "text-text hover:bg-panel-active")}>
        {body}
      </Link>
    );
  }
  return <div className={box}>{body}</div>;
}
