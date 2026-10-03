"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Building2, ChevronsUpDown } from "lucide-react";
import { toast } from "sonner";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { api } from "@/lib/api";
import { useMachinesSummary } from "@/lib/machines";
import { messageFor } from "@/lib/messages";
import { contextName, useCurrentUser } from "@/lib/me-context";

type ClientRow = { id: string; name: string };
type ClientPage = { items: ClientRow[] };

/** Troca o cliente do painel. O servidor valida o cookie `rg_client` a cada requisição. */
export function ClientSwitcher() {
  const me = useCurrentUser();
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [query, setQuery] = useState("");

  // Ctrl+J abre o seletor (design-system.md §5).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.key.toLowerCase() === "j") {
        event.preventDefault();
        setOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(term.trim()), 250);
    return () => clearTimeout(timer);
  }, [term]);

  const clients = useQuery({
    queryKey: ["clients", "switcher", query],
    queryFn: () =>
      api.get<ClientPage>(
        `/clients?per_page=50&sort=name${query ? `&q=${encodeURIComponent(query)}` : ""}`,
      ),
    enabled: open,
  });

  // "N sem sinal" ao lado de cada cliente com máquina parada (design-system.md 5, Seletor de cliente).
  const summary = useMachinesSummary();
  const silentByClient = new Map(summary.data?.clients.map((c) => [c.client_id, c.no_signal]));

  const choose = useMutation({
    mutationFn: (clientId: string | null) => api.put("/auth/context", { client_id: clientId }),
    // Trocar de cliente recarrega a página atual no novo contexto.
    onSuccess: () => window.location.reload(),
    onError: (error) => toast.error(messageFor(error), { duration: Infinity, closeButton: true }),
  });

  const currentId = me.context?.client_id ?? null;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Cliente: ${contextName(me)}. Trocar cliente (Ctrl+J)`}
          className="flex min-h-11 w-full items-center gap-3 rounded-control border border-border-control bg-panel-active px-3 text-left hover:bg-control-hover"
        >
          <Building2 aria-hidden className="size-[18px] shrink-0 text-accent-text" />
          <span className="min-w-0 flex-1">
            <span className="block text-overline uppercase text-text-muted">Cliente</span>
            <span className="block truncate text-body text-text">{contextName(me)}</span>
          </span>
          <ChevronsUpDown aria-hidden className="size-4 shrink-0 text-text-label" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-[272px] p-0">
        <Command shouldFilter={false}>
          <CommandInput placeholder="Buscar cliente" value={term} onValueChange={setTerm} />
          <CommandList>
            {!query && (
              <CommandItem
                value="todos"
                selected={me.context?.all_clients ?? false}
                onSelect={() => choose.mutate(null)}
              >
                Todos os clientes
              </CommandItem>
            )}
            {clients.data?.items.map((client) => (
              <CommandItem
                key={client.id}
                value={client.id}
                selected={client.id === currentId}
                onSelect={() => choose.mutate(client.id)}
              >
                <span className="min-w-0 flex-1 truncate">{client.name}</span>
                {(silentByClient.get(client.id) ?? 0) > 0 && (
                  <span className="shrink-0 text-caption text-danger-text">
                    {silentByClient.get(client.id)} sem sinal
                  </span>
                )}
              </CommandItem>
            ))}
            {clients.isSuccess && clients.data.items.length === 0 && (
              <CommandEmpty>Nenhum cliente encontrado.</CommandEmpty>
            )}
            {clients.isError && (
              <CommandEmpty>Não foi possível carregar os clientes.</CommandEmpty>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
