"use client";

import { useEffect, useState } from "react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

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
    <div className="min-h-screen">
      {/* Abaixo de 900px a sidebar vira painel lateral; fechada, sai da ordem de foco. */}
      <aside
        id="sidebar"
        className={`fixed inset-y-0 left-0 z-30 h-screen w-[248px] border-r border-border bg-sidebar transition-transform min-[900px]:translate-x-0 ${
          open ? "translate-x-0" : "max-[899px]:invisible max-[899px]:-translate-x-full"
        }`}
      >
        <Sidebar onClose={() => setOpen(false)} />
      </aside>
      {open && (
        <div
          aria-hidden
          className="fixed inset-0 z-20 bg-scrim min-[900px]:hidden"
          onClick={() => setOpen(false)}
        />
      )}

      <main className="min-[900px]:pl-[248px]">
        <div className="mx-auto flex max-w-[1240px] flex-col gap-6 px-10 pb-14 pt-6 max-[899px]:px-4">
          <Topbar menuOpen={open} onMenu={() => setOpen((value) => !value)} />
          {children}
        </div>
      </main>
    </div>
  );
}
