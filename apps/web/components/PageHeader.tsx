/** Cabeçalho de página (design-system.md §6): h1 display + texto de até 680px; ações à direita. */
export function PageHeader({
  title,
  description,
  actions,
}: Readonly<{ title: string; description: React.ReactNode; actions?: React.ReactNode }>) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-display max-[899px]:text-[24px]">{title}</h1>
        <p className="mt-1 max-w-[680px] text-body text-text-label">{description}</p>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-3 max-[899px]:w-full [&>*]:max-[899px]:h-12 [&>*]:max-[899px]:w-full">
          {actions}
        </div>}
    </header>
  );
}
