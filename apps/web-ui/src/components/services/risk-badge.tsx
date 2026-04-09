import { Shield, ShieldAlert, ShieldX, ShieldQuestion } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import type { RiskLevel } from "@/types/api";

const riskConfig: Record<
  RiskLevel,
  { label: string; icon: LucideIcon; color: string }
> = {
  safe: {
    label: "Safe",
    icon: Shield,
    color:
      "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  },
  cautious: {
    label: "Cautious",
    icon: ShieldAlert,
    color:
      "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
  },
  dangerous: {
    label: "Dangerous",
    icon: ShieldX,
    color: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  },
  unknown: {
    label: "Unknown",
    icon: ShieldQuestion,
    color: "bg-muted text-muted-foreground",
  },
};

interface RiskBadgeProps {
  level: RiskLevel;
  className?: string;
}

export function RiskBadge({ level, className }: RiskBadgeProps) {
  const config = riskConfig[level];
  const Icon = config.icon;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        config.color,
        className,
      )}
    >
      <Icon className="size-3" />
      {config.label}
    </span>
  );
}
