import { useId } from "react";
import { CircleAlert } from "lucide-react";
import { Label } from "@/components/ui/label";

type ControlProps = {
  id: string;
  "aria-invalid": boolean | undefined;
  "aria-describedby": string | undefined;
};

/**
 * Rótulo sempre visível, ajuda abaixo e mensagem de erro que substitui a ajuda
 * (design-system.md §5, Input). O controle recebe id e aria-* por render prop.
 */
export function Field({
  label,
  help,
  error,
  invalid,
  children,
}: Readonly<{
  label: string;
  help?: string;
  error?: string | null;
  /** Borda de erro sem mensagem própria (ex.: a senha errada do login, que já tem banner). */
  invalid?: boolean;
  children: (props: ControlProps) => React.ReactNode;
}>) {
  const id = useId();
  const noteId = `${id}-note`;
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children({
        id,
        "aria-invalid": error || invalid ? true : undefined,
        "aria-describedby": error || help ? noteId : undefined,
      })}
      {error ? (
        <p id={noteId} role="alert" className="flex items-start gap-1.5 text-caption text-danger-text">
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" />
          {error}
        </p>
      ) : help ? (
        <p id={noteId} className="text-caption text-text-label">
          {help}
        </p>
      ) : null}
    </div>
  );
}
