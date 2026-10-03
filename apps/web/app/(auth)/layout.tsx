import { BellRing, ListChecks, Play, type LucideIcon } from "lucide-react";
import { BrandMark } from "@/components/BrandMark";

const POINTS: { icon: LucideIcon; title: string; text: string }[] = [
  {
    icon: Play,
    title: "Disparo sem acessar a máquina",
    text: "Execute qualquer robô direto do painel, sem abrir acesso remoto.",
  },
  {
    icon: ListChecks,
    title: "Cada item com status e motivo",
    text: "Veja o que deu certo, o que falhou e por quê, linha por linha.",
  },
  {
    icon: BellRing,
    title: "Aviso quando algo para",
    text: "Receba e-mail quando um robô falha ou uma máquina fica sem sinal.",
  },
];

// design-system.md §6: duas colunas (marca 1.1fr / formulário 1fr); abaixo de 960px só o formulário,
// com a marca pequena no topo.
export default function AuthLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <div className="grid min-h-screen min-[960px]:grid-cols-[1.1fr_1fr]">
      <aside className="hidden flex-col justify-between border-r border-border bg-sidebar p-12 min-[960px]:flex">
        <BrandMark />
        <div className="max-w-[520px]">
          <h2 className="text-display">
            Suas automações,{" "}
            <span className="text-accent-text">acompanhadas em tempo real.</span>
          </h2>
          <ul className="mt-10 grid gap-6">
            {POINTS.map(({ icon: Icon, title, text }) => (
              <li key={title} className="flex gap-4">
                <span
                  aria-hidden
                  className="grid size-11 shrink-0 place-items-center rounded-control bg-panel-active text-accent-text"
                >
                  <Icon className="size-5" />
                </span>
                <div>
                  <p className="text-title text-text">{title}</p>
                  <p className="mt-0.5 text-body-sm text-text-label">{text}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-caption text-text-muted">
          Regista é um serviço da Artemisys · artemisys.com.br
        </p>
      </aside>

      <main className="flex flex-col px-4 py-6 min-[960px]:px-12 min-[960px]:py-12">
        <BrandMark className="mb-10 min-[960px]:hidden" />
        <div className="m-auto w-full max-w-[420px]">{children}</div>
      </main>
    </div>
  );
}
