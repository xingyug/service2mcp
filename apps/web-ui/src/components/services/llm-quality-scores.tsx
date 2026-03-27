"use client";

import { cn } from "@/lib/utils";
import { BarChart3, Star } from "lucide-react";

interface LLMQualityScoresProps {
  scores?: {
    accuracy: number;
    completeness: number;
    clarity: number;
    overall: number;
  };
  compact?: boolean;
}

function scoreColor(value: number): string {
  if (value > 0.8) return "text-green-600 dark:text-green-400";
  if (value > 0.5) return "text-yellow-600 dark:text-yellow-400";
  return "text-red-600 dark:text-red-400";
}

function barColor(value: number): string {
  if (value > 0.8) return "bg-green-500 dark:bg-green-400";
  if (value > 0.5) return "bg-yellow-500 dark:bg-yellow-400";
  return "bg-red-500 dark:bg-red-400";
}

function barTrackColor(value: number): string {
  if (value > 0.8) return "bg-green-100 dark:bg-green-900/40";
  if (value > 0.5) return "bg-yellow-100 dark:bg-yellow-900/40";
  return "bg-red-100 dark:bg-red-900/40";
}

function ScoreBar({
  label,
  value,
  compact,
}: {
  label: string;
  value: number;
  compact?: boolean;
}) {
  const pct = Math.round(value * 100);
  return (
    <div className={cn("space-y-1", compact && "space-y-0.5")}>
      <div className="flex items-center justify-between">
        <span
          className={cn(
            "font-medium text-muted-foreground",
            compact ? "text-[10px]" : "text-xs",
          )}
        >
          {label}
        </span>
        <span
          className={cn(
            "tabular-nums font-semibold",
            compact ? "text-[10px]" : "text-xs",
            scoreColor(value),
          )}
        >
          {pct}%
        </span>
      </div>
      <div
        className={cn(
          "overflow-hidden rounded-full",
          compact ? "h-1" : "h-1.5",
          barTrackColor(value),
        )}
      >
        <div
          className={cn(
            "h-full rounded-full transition-all duration-500",
            barColor(value),
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function OverallGauge({
  value,
  compact,
}: {
  value: number;
  compact?: boolean;
}) {
  const pct = Math.round(value * 100);
  // CSS-only ring gauge using conic-gradient emulated with border
  const size = compact ? "size-14" : "size-20";
  const deg = Math.round(value * 360);

  return (
    <div className="flex flex-col items-center gap-1">
      <div
        className={cn(
          "relative flex items-center justify-center rounded-full",
          size,
        )}
        style={{
          background: `conic-gradient(
            ${value > 0.8 ? "var(--color-green-500)" : value > 0.5 ? "var(--color-yellow-500)" : "var(--color-red-500)"} ${deg}deg,
            var(--color-muted) ${deg}deg
          )`,
        }}
      >
        <div
          className={cn(
            "flex items-center justify-center rounded-full bg-card",
            compact ? "size-10" : "size-14",
          )}
        >
          <span
            className={cn(
              "font-bold tabular-nums",
              compact ? "text-sm" : "text-xl",
              scoreColor(value),
            )}
          >
            {pct}
          </span>
        </div>
      </div>
      <span
        className={cn(
          "font-medium text-muted-foreground",
          compact ? "text-[10px]" : "text-xs",
        )}
      >
        Overall
      </span>
    </div>
  );
}

export function LLMQualityScores({
  scores,
  compact = false,
}: LLMQualityScoresProps) {
  if (!scores) {
    return (
      <div
        className={cn(
          "flex items-center gap-2 text-muted-foreground",
          compact ? "text-xs" : "text-sm",
        )}
      >
        <BarChart3 className={compact ? "size-3.5" : "size-4"} />
        <span>Quality scores N/A</span>
      </div>
    );
  }

  const dimensions: { label: string; value: number }[] = [
    { label: "Accuracy", value: scores.accuracy },
    { label: "Completeness", value: scores.completeness },
    { label: "Clarity", value: scores.clarity },
  ];

  if (compact) {
    return (
      <div className="flex items-center gap-3">
        <OverallGauge value={scores.overall} compact />
        <div className="flex-1 space-y-1.5">
          {dimensions.map((d) => (
            <ScoreBar key={d.label} label={d.label} value={d.value} compact />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <Star className="size-4" />
        LLM Quality Scores
      </div>
      <div className="flex items-start gap-6">
        <OverallGauge value={scores.overall} />
        <div className="flex-1 space-y-2.5">
          {dimensions.map((d) => (
            <ScoreBar key={d.label} label={d.label} value={d.value} />
          ))}
        </div>
      </div>
    </div>
  );
}
