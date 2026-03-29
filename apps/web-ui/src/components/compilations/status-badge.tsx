"use client";

import { cn } from "@/lib/utils";
import { formatCompilationStatus } from "@/lib/compilation-status";
import type { CompilationStatus } from "@/types/api";

const statusConfig: Record<
  CompilationStatus,
  { label: string; className: string }
> = {
  pending: {
    label: "Pending",
    className: "bg-muted text-muted-foreground",
  },
  running: {
    label: "Running",
    className: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  },
  succeeded: {
    label: "Succeeded",
    className: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  },
  failed: {
    label: "Failed",
    className: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  },
  rolled_back: {
    label: "Rolled Back",
    className:
      "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
  },
};

export function StatusBadge({
  status,
  className,
}: {
  status: CompilationStatus;
  className?: string;
}) {
  const config = statusConfig[status] ?? {
    label: formatCompilationStatus(status),
    className: "bg-muted text-muted-foreground",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        config.className,
        className,
      )}
    >
      {config.label}
    </span>
  );
}
