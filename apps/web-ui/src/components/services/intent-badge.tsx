import { Telescope, Zap } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ToolIntent } from "@/types/api";

interface IntentBadgeProps {
  intent?: ToolIntent;
  className?: string;
}

export function IntentBadge({ intent, className }: IntentBadgeProps) {
  if (!intent) return null;

  const isDiscovery = intent === "discovery";
  const Icon = isDiscovery ? Telescope : Zap;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide",
        isDiscovery
          ? "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300"
          : "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
        className,
      )}
    >
      <Icon className="size-3" />
      {intent}
    </span>
  );
}
