"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronsUpDown, LogOut, MonitorSmartphone, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { roleLabel, type Me } from "@/lib/me";

function initials(me: Me): string {
  const source = me.is_platform_admin && me.display_name ? me.display_name : me.email.split("@")[0]!;
  const words = source.split(/[\s._-]+/).filter(Boolean);
  const letters =
    words.length > 1 ? `${words[0]![0]}${words[1]![0]}` : source.slice(0, 2);
  return letters.toUpperCase();
}

/** Rodapé da barra lateral: avatar com iniciais, identidade, papel e menu da conta. */
export function UserMenu({ me }: Readonly<{ me: Me }>) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const identity = me.is_platform_admin ? (me.display_name ?? me.email) : me.email;

  const logout = useMutation({
    mutationFn: () => api.post("/auth/logout"),
    onSettled: () => {
      queryClient.clear();
      router.replace("/login");
    },
  });

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label="Menu da conta"
          className="flex min-h-14 w-full items-center gap-3 rounded-control px-2 text-left hover:bg-panel-active"
        >
          <span
            aria-hidden
            className="grid size-9 shrink-0 place-items-center rounded-full bg-accent text-caption font-semibold text-accent-fg"
          >
            {initials(me)}
          </span>
          <span className="min-w-0 flex-1 leading-tight">
            <span className="block truncate text-body-sm text-text">{identity}</span>
            <span className="block text-caption text-text-muted">{roleLabel(me)}</span>
          </span>
          <ChevronsUpDown aria-hidden className="size-4 shrink-0 text-text-label" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-60">
        <DropdownMenuItem asChild>
          <Link href="/account/sessions">
            <MonitorSmartphone aria-hidden />
            Minhas sessões
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/account/sessions#verificacao">
            <ShieldCheck aria-hidden />
            Configurar MFA
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => logout.mutate()}>
          <LogOut aria-hidden />
          Sair
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
