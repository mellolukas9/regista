import { Bell, Menu, Search } from "lucide-react";

export function Topbar({
  menuOpen,
  onMenu,
}: Readonly<{ menuOpen: boolean; onMenu: () => void }>) {
  return (
    <div className="flex min-h-11 flex-wrap items-center gap-3 max-[899px]:min-h-[60px]">
      <button
        type="button"
        aria-label={menuOpen ? "Fechar menu" : "Abrir menu"}
        aria-expanded={menuOpen}
        aria-controls="sidebar"
        onClick={onMenu}
        className="grid size-11 place-items-center rounded-control border border-border-control text-text-secondary hover:bg-control-hover min-[900px]:hidden"
      >
        <Menu aria-hidden className="size-5" />
      </button>

      <p className="mr-auto font-mono text-caption text-text-label">Regista / Início</p>

      <p className="flex items-center gap-2 text-caption text-text-label">
        <span aria-hidden className="size-2 rounded-full bg-success" />
        Sincronizado
      </p>

      {/* Busca e notificações chegam com os marcos que têm dados (M3 e M7). */}
      <button
        type="button"
        disabled
        className="flex h-11 min-w-0 items-center gap-2 rounded-control border border-border-control bg-sidebar px-3 text-body-sm text-text-muted disabled:cursor-not-allowed max-[899px]:hidden"
      >
        <Search aria-hidden className="size-4" />
        Buscar referência, bot ou execução
        <kbd className="rounded-sm border border-border-control px-1.5 font-mono text-caption">
          Ctrl K
        </kbd>
      </button>
      <button
        type="button"
        disabled
        aria-label="Notificações"
        className="grid size-11 place-items-center rounded-control border border-border-control text-text-muted disabled:cursor-not-allowed"
      >
        <Bell aria-hidden className="size-[18px]" />
      </button>
    </div>
  );
}
