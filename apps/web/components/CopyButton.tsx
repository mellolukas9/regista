"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";

/** "Copiar chave" vira "Copiada" por 3 s, anunciado por aria-live (design-system.md §5, KeyReveal). */
export function CopyButton({
  value,
  label,
  copiedLabel,
  className,
}: Readonly<{ value: string; label: string; copiedLabel: string; className?: string }>) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      return; // sem permissão da área de transferência: o texto continua visível para copiar à mão
    }
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 3000);
  }

  return (
    <Button
      type="button"
      variant="secondary"
      icon={copied ? Check : Copy}
      onClick={copy}
      aria-live="polite"
      className={className}
    >
      {copied ? copiedLabel : label}
    </Button>
  );
}
