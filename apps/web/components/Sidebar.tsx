"use client";

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
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BrandMark } from "@/components/BrandMark";
import { ClientSwitcher } from "@/components/ClientSwitcher";
import { UserMenu } from "@/components/UserMenu";
import { useJobsSummary } from "@/lib/jobs";
import { useMachinesSummary } from "@/lib/machines";
import { canManageUsers } from "@/lib/me";
import { useCurrentUser } from "@/lib/me-context";
import { cn } from "@/lib/utils";

type Item = {
  label: string;
  icon: LucideIcon;
  /** Sem `href` o item ainda não tem tela: pertence a um marco futuro. */
  href?: string;
  /** Contador de atenção ao lado do rótulo (design-system.md 11.6, "Contadores da navegação"). */
  counter?: "machines" | "jobs";
  visible?: (me: ReturnType<typeof useCurrentUser>) => boolean;
};
type Group = { title: string; items: Item[]; visible?: Item["visible"] };

// Estrutura do design-system.md (Sidebar). Itens sem rota ficam como no M0 até o marco que os entrega;
// a navegação é filtrada por papel, e o servidor aplica as mesmas permissões.
const GROUPS: Group[] = [
  {
    title: "Artemisys",
    visible: (me) => me.is_platform_admin,
    items: [{ label: "Clientes", icon: Building2, href: "/clients" }],
  },
  {
    title: "Operação",
    items: [
      { label: "Dashboard", icon: LayoutDashboard },
      { label: "Execuções", icon: Play, href: "/runs", counter: "jobs" },
      { label: "Filas", icon: ListChecks },
      { label: "Lotes", icon: Layers },
    ],
  },
  {
    title: "Automação",
    items: [
      { label: "Bots", icon: Bot, href: "/bots" },
      { label: "Agendamentos", icon: CalendarClock },
      { label: "Alertas", icon: Bell },
    ],
  },
  {
    title: "Infraestrutura",
    items: [
      { label: "Máquinas", icon: MonitorCog, href: "/machines", counter: "machines" },
      { label: "Usuários", icon: Users, href: "/users", visible: canManageUsers },
    ],
  },
];

const ITEM_BASE = "flex h-11 items-center gap-3 rounded-control px-3 text-body";

export function Sidebar({ onClose }: Readonly<{ onClose: () => void }>) {
  const me = useCurrentUser();
  const pathname = usePathname();
  const machines = useMachinesSummary();
  const jobs = useJobsSummary();
  // Só contam o que pede ação: máquinas sem sinal (perigo) e execuções esperando máquina (atenção).
  const counters = { machines: machines.data?.no_signal ?? 0, jobs: jobs.data?.pending ?? 0 };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 px-5 py-5">
        <Link href="/" aria-label="Regista, início" onClick={onClose}>
          <BrandMark />
        </Link>
        <button
          type="button"
          aria-label="Fechar menu"
          onClick={onClose}
          className="grid size-11 place-items-center rounded-control border border-border-control text-text-secondary hover:bg-control-hover min-[900px]:hidden"
        >
          <X aria-hidden className="size-5" />
        </button>
      </div>

      <div className="px-3 pb-4">
        {me.is_platform_admin ? (
          <ClientSwitcher />
        ) : (
          <div className="flex min-h-11 items-center gap-3 rounded-control border border-border-control bg-panel-active px-3">
            <Building2 aria-hidden className="size-[18px] shrink-0 text-accent-text" />
            <span className="min-w-0">
              <span className="block text-overline uppercase text-text-muted">Cliente</span>
              <span className="block truncate text-body text-text">{me.tenant_name}</span>
            </span>
          </div>
        )}
      </div>

      <nav aria-label="Principal" className="flex-1 overflow-y-auto px-3 pb-6">
        <div className="flex flex-col gap-5">
          {GROUPS.filter((group) => group.visible?.(me) ?? true).map((group) => (
            <div key={group.title}>
              <p className="px-3 pb-1 text-overline uppercase text-text-muted">{group.title}</p>
              <ul className="flex flex-col gap-0.5">
                {group.items
                  .filter((item) => item.visible?.(me) ?? true)
                  .map(({ label, icon: Icon, href, counter }) => (
                    <li key={label}>
                      {href ? (
                        <NavLink href={href} pathname={pathname} onClick={onClose}>
                          <Icon aria-hidden className="size-[18px]" />
                          {label}
                          {counter && counters[counter] > 0 && (
                            <span
                              aria-label={counterLabel(counter, counters[counter])}
                              className={cn(
                                "ml-auto grid h-5 min-w-5 place-items-center rounded-pill px-1.5 font-mono text-caption font-medium text-danger-fg",
                                counter === "machines" ? "bg-danger" : "bg-warning",
                              )}
                            >
                              {counters[counter]}
                            </span>
                          )}
                        </NavLink>
                      ) : (
                        <span
                          aria-disabled="true"
                          className={cn(ITEM_BASE, "cursor-not-allowed text-text-label")}
                        >
                          <Icon aria-hidden className="size-[18px]" />
                          {label}
                        </span>
                      )}
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </div>
      </nav>

      <div className="border-t border-border p-3">
        <UserMenu me={me} />
      </div>
    </div>
  );
}

function counterLabel(counter: "machines" | "jobs", n: number): string {
  if (counter === "machines") return `${n} ${n === 1 ? "máquina sem sinal" : "máquinas sem sinal"}`;
  return `${n} ${n === 1 ? "execução pendente" : "execuções pendentes"}`;
}

function NavLink({
  href,
  pathname,
  onClick,
  children,
}: Readonly<{
  href: string;
  pathname: string;
  onClick: () => void;
  children: React.ReactNode;
}>) {
  const active = pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(
        ITEM_BASE,
        "text-text-secondary hover:bg-panel-active",
        active &&
          "bg-panel-active text-text shadow-[inset_0_0_0_1px_var(--color-border-control)] [&_svg]:text-accent-text",
      )}
    >
      {children}
    </Link>
  );
}
