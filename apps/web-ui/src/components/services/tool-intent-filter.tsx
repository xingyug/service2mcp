"use client";

import * as React from "react";
import { Telescope, Zap, List } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { Operation, ToolIntent } from "@/types/api";

type FilterValue = "all" | ToolIntent;

interface ToolIntentFilterProps {
  operations: Operation[];
  onFilterChange: (filtered: Operation[]) => void;
}

export function ToolIntentFilter({
  operations,
  onFilterChange,
}: ToolIntentFilterProps) {
  const [active, setActive] = React.useState<FilterValue>("all");

  const counts = React.useMemo(() => {
    const c = { all: operations.length, discovery: 0, action: 0 };
    for (const op of operations) {
      if (op.tool_intent === "discovery") c.discovery++;
      else if (op.tool_intent === "action") c.action++;
    }
    return c;
  }, [operations]);

  const applyFilter = React.useCallback(
    (value: FilterValue) => {
      setActive(value);
      if (value === "all") {
        onFilterChange(operations);
      } else {
        onFilterChange(operations.filter((op) => op.tool_intent === value));
      }
    },
    [operations, onFilterChange],
  );

  // Sync filter when operations change
  React.useEffect(() => {
    applyFilter(active);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [operations]);

  const toggles: {
    value: FilterValue;
    label: string;
    icon: React.ElementType;
    color: string;
    activeColor: string;
  }[] = [
    {
      value: "all",
      label: "All",
      icon: List,
      color: "",
      activeColor: "bg-primary text-primary-foreground",
    },
    {
      value: "discovery",
      label: "Discovery",
      icon: Telescope,
      color: "text-blue-600 dark:text-blue-400",
      activeColor:
        "bg-blue-600 text-white dark:bg-blue-500 dark:text-white",
    },
    {
      value: "action",
      label: "Action",
      icon: Zap,
      color: "text-orange-600 dark:text-orange-400",
      activeColor:
        "bg-orange-600 text-white dark:bg-orange-500 dark:text-white",
    },
  ];

  return (
    <div className="flex items-center gap-1">
      {toggles.map((t) => {
        const Icon = t.icon;
        const isActive = active === t.value;
        return (
          <Button
            key={t.value}
            variant={isActive ? "default" : "outline"}
            size="sm"
            className={cn(
              "gap-1.5",
              isActive && t.activeColor,
              !isActive && t.color,
            )}
            onClick={() => applyFilter(t.value)}
          >
            <Icon className="size-3.5" />
            {t.label}
            <Badge
              variant="secondary"
              className={cn(
                "ml-0.5 tabular-nums",
                isActive &&
                  "border-transparent bg-white/20 text-inherit",
              )}
            >
              {counts[t.value]}
            </Badge>
          </Button>
        );
      })}
    </div>
  );
}
