"use client";

import * as React from "react";
import {
  Shield,
  ShieldAlert,
  ShieldX,
  ShieldQuestion,
  AlertTriangle,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { Operation, RiskLevel } from "@/types/api";

interface RiskFilterProps {
  operations: Operation[];
  onFilterChange: (filtered: Operation[]) => void;
}

const riskConfig: Record<
  RiskLevel,
  {
    label: string;
    icon: React.ElementType;
    color: string;
    activeColor: string;
  }
> = {
  safe: {
    label: "Safe",
    icon: Shield,
    color: "text-green-600 dark:text-green-400",
    activeColor:
      "bg-green-600 text-white dark:bg-green-500 dark:text-white",
  },
  cautious: {
    label: "Cautious",
    icon: ShieldAlert,
    color: "text-yellow-600 dark:text-yellow-400",
    activeColor:
      "bg-yellow-600 text-white dark:bg-yellow-500 dark:text-white",
  },
  dangerous: {
    label: "Dangerous",
    icon: ShieldX,
    color: "text-red-600 dark:text-red-400",
    activeColor:
      "bg-red-600 text-white dark:bg-red-500 dark:text-white",
  },
  unknown: {
    label: "Unknown",
    icon: ShieldQuestion,
    color: "text-muted-foreground",
    activeColor: "bg-muted-foreground text-white",
  },
};

const RISK_LEVELS: RiskLevel[] = ["safe", "cautious", "dangerous", "unknown"];

export function RiskFilter({ operations, onFilterChange }: RiskFilterProps) {
  const [activeFilters, setActiveFilters] = React.useState<Set<RiskLevel>>(
    () => new Set(RISK_LEVELS),
  );

  const counts = React.useMemo(() => {
    const c: Record<RiskLevel, number> = {
      safe: 0,
      cautious: 0,
      dangerous: 0,
      unknown: 0,
    };
    for (const op of operations) c[op.risk.risk_level]++;
    return c;
  }, [operations]);

  const hasDangerous = counts.dangerous > 0;

  const applyFilter = React.useCallback(
    (filters: Set<RiskLevel>) => {
      if (filters.size === RISK_LEVELS.length) {
        onFilterChange(operations);
      } else {
        onFilterChange(
          operations.filter((op) => filters.has(op.risk.risk_level)),
        );
      }
    },
    [operations, onFilterChange],
  );

  const toggle = (level: RiskLevel) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(level)) {
        // Don't allow deselecting all
        if (next.size > 1) next.delete(level);
      } else {
        next.add(level);
      }
      applyFilter(next);
      return next;
    });
  };

  // Sync filter when operations change
  React.useEffect(() => {
    applyFilter(activeFilters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [operations]);

  return (
    <div className="space-y-2">
      {/* Warning banner for dangerous operations */}
      {hasDangerous && (
        <div className="flex items-center gap-2 rounded-lg border border-yellow-300 bg-yellow-50 px-3 py-2 text-sm text-yellow-800 dark:border-yellow-700 dark:bg-yellow-900/20 dark:text-yellow-300">
          <AlertTriangle className="size-4 shrink-0" />
          <span>
            <strong>{counts.dangerous}</strong> dangerous operation
            {counts.dangerous !== 1 ? "s" : ""} detected — review carefully
            before invocation.
          </span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {/* Toggle buttons */}
        <div className="flex items-center gap-1">
          {RISK_LEVELS.map((level) => {
            const cfg = riskConfig[level];
            const Icon = cfg.icon;
            const isActive = activeFilters.has(level);
            return (
              <Button
                key={level}
                variant={isActive ? "default" : "outline"}
                size="sm"
                className={cn(
                  "gap-1.5",
                  isActive && cfg.activeColor,
                  !isActive && cfg.color,
                )}
                onClick={() => toggle(level)}
              >
                <Icon className="size-3.5" />
                {cfg.label}
                <Badge
                  variant="secondary"
                  className={cn(
                    "ml-0.5 tabular-nums",
                    isActive &&
                      "border-transparent bg-white/20 text-inherit",
                  )}
                >
                  {counts[level]}
                </Badge>
              </Button>
            );
          })}
        </div>

        {/* Summary text */}
        <span className="text-xs text-muted-foreground">
          {counts.safe} safe, {counts.cautious} cautious, {counts.dangerous}{" "}
          dangerous, {counts.unknown} unknown
        </span>
      </div>
    </div>
  );
}
