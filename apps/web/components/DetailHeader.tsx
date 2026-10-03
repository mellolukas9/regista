import { ArrowLeft } from "lucide-react";
import Link from "next/link";

/**
 * DetailHeader (design-system.md §5): link de volta com o nome da lista, contexto mono, título
 * display, StatusPill + metadado, ações à direita (descem em largura total abaixo de 900px) e,
 * logo abaixo, a faixa `meta` de pares rótulo/valor.
 */
export function DetailHeader({
  backHref,
  backLabel,
  context,
  title,
  status,
  note,
  actions,
  meta,
}: Readonly<{
  backHref: string;
  backLabel: string;
  context: string;
  title: string;
  status: React.ReactNode;
  note?: React.ReactNode;
  actions?: React.ReactNode;
  meta?: { label: string; value: React.ReactNode }[];
}>) {
  return (
    <header className="grid gap-4">
      <Link href={backHref} className="inline-flex w-fit items-center gap-1.5 text-body-sm">
        <ArrowLeft aria-hidden className="size-4" />
        {backLabel}
      </Link>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="font-mono text-caption text-text-label">{context}</p>
          <h1 className="break-all font-mono text-display max-[899px]:text-[24px]">{title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            {status}
            {note && <span className="font-mono text-caption text-text-label">{note}</span>}
          </div>
        </div>
        {actions && (
          <div className="flex flex-wrap items-center gap-3 max-[899px]:w-full [&>*]:max-[899px]:w-full">
            {actions}
          </div>
        )}
      </div>
      {meta && meta.length > 0 && (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-card border border-border bg-panel p-4 min-[900px]:grid-cols-5">
          {meta.map((item) => (
            <div key={item.label} className="min-w-0">
              <dt className="text-overline uppercase text-text-muted">{item.label}</dt>
              <dd className="mt-0.5 break-words text-body text-text">{item.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </header>
  );
}
