// Datas em America/Sao_Paulo (design-system.md, regra 10): `dd/MM/yyyy HH:mm`; nas listas, `dd/MM HH:mm`.
const TIME_ZONE = "America/Sao_Paulo";

function parts(iso: string): Record<string, string> {
  const formatter = new Intl.DateTimeFormat("pt-BR", {
    timeZone: TIME_ZONE,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  return Object.fromEntries(formatter.formatToParts(new Date(iso)).map((p) => [p.type, p.value]));
}

export function formatDate(iso: string): string {
  const p = parts(iso);
  return `${p.day}/${p.month}/${p.year}`;
}

export function formatDateTime(iso: string): string {
  const p = parts(iso);
  return `${p.day}/${p.month}/${p.year} ${p.hour}:${p.minute}`;
}

/** `dd/MM/yyyy HH:mm:ss`, para o cabeçalho de uma execução. */
export function formatDateTimeSeconds(iso: string): string {
  const formatter = new Intl.DateTimeFormat("pt-BR", {
    timeZone: TIME_ZONE,
    second: "2-digit",
  });
  const seconds = formatter.formatToParts(new Date(iso)).find((p) => p.type === "second")?.value ?? "00";
  return `${formatDateTime(iso)}:${seconds.padStart(2, "0")}`;
}

/** Para listas: `dd/MM HH:mm`. */
export function formatListDate(iso: string): string {
  const p = parts(iso);
  return `${p.day}/${p.month} ${p.hour}:${p.minute}`;
}

const MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

/** `ago/2026`, usado em "desde ago/2026". */
export function formatSince(iso: string): string {
  const p = parts(iso);
  return `${MONTHS[Number(p.month) - 1] ?? p.month}/${p.year}`;
}

/** "há 12 min", "há 2 h", "há 3 dias". A data completa vai num Tooltip quando couber. */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "agora há pouco";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `há ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `há ${hours} h`;
  const days = Math.round(hours / 24);
  return `há ${days} ${days === 1 ? "dia" : "dias"}`;
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat("pt-BR").format(value);
}
