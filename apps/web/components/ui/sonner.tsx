"use client";

import { CircleCheck, Info, Loader2, OctagonX, TriangleAlert } from "lucide-react";
import { Toaster as Sonner, type ToasterProps } from "sonner";

// design-system.md §5 (Toast): canto inferior direito, no máximo 3; só tema escuro por enquanto.
function Toaster(props: ToasterProps) {
  return (
    <Sonner
      theme="dark"
      position="bottom-right"
      visibleToasts={3}
      icons={{
        success: <CircleCheck className="size-4 text-success" />,
        info: <Info className="size-4 text-accent-text" />,
        warning: <TriangleAlert className="size-4 text-warning" />,
        error: <OctagonX className="size-4 text-danger" />,
        loading: <Loader2 className="size-4 animate-spin" />,
      }}
      style={
        {
          "--normal-bg": "var(--color-panel-active)",
          "--normal-text": "var(--color-text)",
          "--normal-border": "var(--color-border-control)",
          "--border-radius": "var(--radius-control)",
        } as React.CSSProperties
      }
      {...props}
    />
  );
}

export { Toaster };
