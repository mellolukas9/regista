import * as React from "react";
import { cn } from "@/lib/utils";

// design-system.md §5 (Card): raio 14px, padding 20px.
function Card({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card"
      className={cn("rounded-card border border-border bg-panel p-5", className)}
      {...props}
    />
  );
}

function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-header"
      className={cn("flex flex-wrap items-start justify-between gap-3", className)}
      {...props}
    />
  );
}

function CardTitle({ className, ...props }: React.ComponentProps<"h2">) {
  return <h2 data-slot="card-title" className={cn("text-title text-text", className)} {...props} />;
}

function CardDescription({ className, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      data-slot="card-description"
      className={cn("mt-1 text-body-sm text-text-label", className)}
      {...props}
    />
  );
}

export { Card, CardHeader, CardTitle, CardDescription };
