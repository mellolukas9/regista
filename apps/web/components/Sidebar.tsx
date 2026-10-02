import {
  Bell,
  Bot,
  Building2,
  CalendarClock,
  Layers,
  LayoutDashboard,
  ListChecks,
  MonitorCog,
  Play,
  Users,
  X,
  type LucideIcon,
} from "lucide-react";

type Item = { label: string; icon: LucideIcon };
type Group = { title: string; items: Item[] };

// Estrutura do design-system.md (Sidebar). No M0 os itens ainda não têm rota.
const GROUPS: Group[] = [
  { title: "Artemisys", items: [{ label: "Clientes", icon: Building2 }] },
  {
    title: "Operação",
    items: [
      { label: "Dashboard", icon: LayoutDashboard },
      { label: "Execuções", icon: Play },
      { label: "Filas", icon: ListChecks },
      { label: "Lotes", icon: Layers },
    ],
  },
  {
    title: "Automação",
    items: [
      { label: "Bots", icon: Bot },
      { label: "Agendamentos", icon: CalendarClock },
      { label: "Alertas", icon: Bell },
    ],
  },
  {
    title: "Infraestrutura",
    items: [
      { label: "Máquinas", icon: MonitorCog },
      { label: "Usuários", icon: Users },
    ],
  },
];

export function Sidebar({ onClose }: Readonly<{ onClose: () => void }>) {
  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="flex items-center gap-3 px-5 py-5">
        <span
          aria-hidden
          className="grid size-8 place-items-center rounded-control bg-accent text-body-sm font-semibold text-accent-fg"
        >
          R
        </span>
        <span className="leading-tight">
          <span className="block text-title">Regista</span>
          <span className="block text-caption text-text-muted">by Artemisys</span>
        </span>
        <button
          type="button"
          aria-label="Fechar menu"
          onClick={onClose}
          className="ml-auto grid size-11 place-items-center rounded-control border border-border-control text-text-secondary hover:bg-control-hover min-[900px]:hidden"
        >
          <X aria-hidden className="size-5" />
        </button>
      </div>
      <nav aria-label="Principal" className="flex flex-col gap-5 px-3 pb-6">
        {GROUPS.map((group) => (
          <div key={group.title}>
            <p className="px-3 pb-1 text-overline uppercase text-text-muted">{group.title}</p>
            <ul className="flex flex-col gap-0.5">
              {group.items.map(({ label, icon: Icon }) => (
                <li key={label}>
                  <span
                    aria-disabled="true"
                    className="flex h-11 cursor-not-allowed items-center gap-3 rounded-control px-3 text-body text-text-label"
                  >
                    <Icon aria-hidden className="size-[18px]" />
                    {label}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>
    </div>
  );
}
