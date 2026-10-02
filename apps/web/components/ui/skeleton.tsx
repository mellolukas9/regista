import { cn } from "@/lib/utils";

function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden
      className={cn("animate-skeleton rounded-control bg-panel-active", className)}
      {...props}
    />
  );
}

export { Skeleton };
