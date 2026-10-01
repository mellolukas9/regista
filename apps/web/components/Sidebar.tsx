export function Sidebar() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center gap-2.5 px-5">
        <span aria-hidden className="size-3 rounded-[4px] bg-accent" />
        <span className="text-[15px] font-semibold tracking-tight">Regista</span>
      </div>
      <nav aria-label="Principal" className="flex-1 px-3 py-2">
        <p className="px-2 text-xs font-medium uppercase tracking-wider text-faint">Operação</p>
      </nav>
    </div>
  );
}
