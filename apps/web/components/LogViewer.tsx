"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SegmentedControl, SegmentedItem } from "@/components/ui/toggle-group";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api";
import { jobsKey, type LogLine, type LogPage } from "@/lib/jobs";
import { POLL_MS } from "@/lib/machines";
import { cn } from "@/lib/utils";

type Level = LogLine["level"];
type Filter = "all" | Level;
type Loaded = { lines: LogLine[]; cursor: number; counts: LogPage["level_counts"] };

const PAGE = 1000;
const LIVE_MS = 3000;
const ZERO = { INFO: 0, WARN: 0, ERROR: 0 } as const;

const LEVEL_TEXT: Record<Level, string> = {
  INFO: "text-accent-text",
  WARN: "text-log-warn",
  ERROR: "text-danger",
};

const TIME = new Intl.DateTimeFormat("pt-BR", {
  timeZone: "America/Sao_Paulo",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  fractionalSecondDigits: 3,
  hourCycle: "h23",
});

function clock(iso: string): string {
  return TIME.format(new Date(iso));
}

/**
 * LogViewer (design-system.md §5): fundo `--color-log`, mono 12.5/20, hora, nível e mensagem. O texto
 * vem do robô e não é confiável: entra como texto no React, nunca como HTML. Em execução ativa,
 * "Acompanhar ao vivo" busca só as linhas novas a cada poucos segundos.
 */
export function LogViewer({
  jobId,
  active,
  contextKey,
}: Readonly<{ jobId: string; active: boolean; contextKey: string }>) {
  const queryClient = useQueryClient();
  const key = [...jobsKey, "logs", jobId, contextKey];
  const [live, setLive] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");
  const [copied, setCopied] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const stuckToBottom = useRef(true);

  const logs = useQuery({
    queryKey: key,
    // Cada busca parte da última linha já guardada e junta o que veio: só as linhas novas trafegam.
    queryFn: async (): Promise<Loaded> => {
      const previous = queryClient.getQueryData<Loaded>(key) ?? { lines: [], cursor: 0, counts: ZERO };
      const lines = [...previous.lines];
      let cursor = previous.cursor;
      let counts = previous.counts;
      for (;;) {
        const page = await api.get<LogPage>(`/jobs/${jobId}/logs?after_seq=${cursor}&limit=${PAGE}`);
        lines.push(...page.items);
        counts = page.level_counts;
        const last = page.items.at(-1);
        if (last) cursor = last.seq;
        if (!last || !page.has_more) break;
      }
      return { lines, cursor, counts };
    },
    structuralSharing: false,
    refetchInterval: active ? (live ? LIVE_MS : POLL_MS) : false,
  });

  const lines = logs.data?.lines ?? [];
  const counts = logs.data?.counts ?? ZERO;
  const shown = filter === "all" ? lines : lines.filter((line) => line.level === filter);

  // Acompanhando ao vivo, a rolagem fica no fim, a menos que a pessoa tenha subido para ler.
  useEffect(() => {
    const element = box.current;
    if (element && live && active && stuckToBottom.current) element.scrollTop = element.scrollHeight;
  }, [shown.length, live, active]);

  async function copy() {
    const text = shown.map((line) => `${clock(line.ts)} ${line.level} ${line.message}`).join("\n");
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return;
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 3000);
  }

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <SegmentedControl
          aria-label="Nível"
          value={filter}
          onValueChange={(next) => setFilter(next as Filter)}
          className="w-auto"
        >
          <SegmentedItem value="all" count={counts.INFO + counts.WARN + counts.ERROR}>
            Todos
          </SegmentedItem>
          <SegmentedItem value="INFO" count={counts.INFO}>
            INFO
          </SegmentedItem>
          <SegmentedItem value="WARN" count={counts.WARN}>
            WARN
          </SegmentedItem>
          <SegmentedItem value="ERROR" count={counts.ERROR}>
            ERROR
          </SegmentedItem>
        </SegmentedControl>

        <div className="ml-auto flex flex-wrap items-center gap-4">
          {active && (
            <label className="flex min-h-11 items-center gap-2 text-body-sm text-text-secondary">
              <Switch checked={live} onCheckedChange={setLive} aria-label="Acompanhar ao vivo" />
              Acompanhar ao vivo
            </label>
          )}
          <Button
            type="button"
            variant="secondary"
            icon={copied ? Check : Copy}
            onClick={copy}
            disabled={shown.length === 0}
            aria-live="polite"
          >
            {copied ? "Copiados" : "Copiar logs"}
          </Button>
        </div>
      </div>

      <div
        ref={box}
        role="log"
        aria-label="Logs da execução"
        onScroll={(event) => {
          const el = event.currentTarget;
          stuckToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        className="max-h-[70vh] min-h-[420px] overflow-auto rounded-card border border-border bg-log p-4 font-mono text-[12.5px] leading-5"
      >
        {logs.isPending ? (
          <p className="text-text-label">Carregando logs_</p>
        ) : logs.isError && lines.length === 0 ? (
          <div className="grid justify-items-start gap-3">
            <p className="text-danger">Não foi possível carregar os logs.</p>
            <Button type="button" variant="secondary" onClick={() => logs.refetch()}>
              Tentar de novo
            </Button>
          </div>
        ) : lines.length === 0 ? (
          <p className="text-text-label">
            {active ? "Aguardando a primeira linha do robô_" : "Esta execução não gerou logs."}
          </p>
        ) : shown.length === 0 ? (
          <div className="grid justify-items-start gap-3">
            <p className="text-text-label">Nenhuma linha com esse filtro.</p>
            <Button type="button" variant="secondary" onClick={() => setFilter("all")}>
              Limpar filtros
            </Button>
          </div>
        ) : (
          <>
            <ol>
              {shown.map((line) => (
                <li
                  key={line.seq}
                  className={cn(
                    "grid grid-cols-[auto_44px_1fr] gap-x-3 px-1 max-[560px]:grid-cols-[44px_1fr]",
                    line.level === "ERROR" && "bg-danger/[0.07]",
                  )}
                >
                  <time
                    dateTime={line.ts}
                    className="text-text-muted max-[560px]:col-span-2"
                  >
                    {clock(line.ts)}
                  </time>
                  <span className={cn("font-medium", LEVEL_TEXT[line.level])}>{line.level}</span>
                  <span
                    className={cn(
                      "whitespace-pre-wrap break-words",
                      line.level === "ERROR"
                        ? "text-log-error"
                        : line.level === "WARN"
                          ? "text-log-warn"
                          : "text-text",
                    )}
                  >
                    {line.message}
                  </span>
                </li>
              ))}
            </ol>
            <p className="mt-2 px-1 text-text-label">
              {active ? (live ? "Acompanhando ao vivo…" : "Atualizando a cada 15 s.") : "Fim dos logs desta execução."}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
