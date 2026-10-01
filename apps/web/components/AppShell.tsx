"use client";

import { useEffect, useState } from "react";
import { Sidebar } from "./Sidebar";

export function AppShell({ children }: Readonly<{ children: React.ReactNode }>) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="min-h-dvh">
      <aside
        id="sidebar"
        className={`fixed inset-y-0 left-0 z-30 w-[248px] border-r border-border bg-sidebar transition-transform min-[900px]:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <Sidebar />
      </aside>
      {open && (
        <div
          aria-hidden
          className="fixed inset-0 z-20 bg-black/60 min-[900px]:hidden"
          onClick={() => setOpen(false)}
        />
      )}

      <div className="min-[900px]:pl-[248px]">
        <header className="flex h-16 items-center gap-3 border-b border-border px-5">
          <button
            type="button"
            aria-label={open ? "Fechar menu" : "Abrir menu"}
            aria-expanded={open}
            aria-controls="sidebar"
            onClick={() => setOpen((value) => !value)}
            className="grid size-11 place-items-center rounded-control border border-border-strong text-text-2 hover:bg-panel-2 min-[900px]:hidden"
          >
            <span aria-hidden className="text-lg leading-none">
              ☰
            </span>
          </button>
          <p className="text-sm text-muted">Regista / Início</p>
        </header>
        <main className="mx-auto w-full max-w-[1240px] px-5 py-8">
          <h1 className="mb-6 text-2xl font-semibold tracking-tight">Painel</h1>
          {children}
        </main>
      </div>
    </div>
  );
}
