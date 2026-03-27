"use client";

import {
  CheckCircle2,
  Circle,
  Loader2,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { CompilationStage, CompilationStatus } from "@/types/api";

const STAGES: { key: CompilationStage; label: string }[] = [
  { key: "detect", label: "Detect" },
  { key: "extract", label: "Extract" },
  { key: "enhance", label: "Enhance" },
  { key: "validate_ir", label: "Validate IR" },
  { key: "generate", label: "Generate" },
  { key: "build", label: "Build" },
  { key: "deploy", label: "Deploy" },
  { key: "validate_runtime", label: "Validate Runtime" },
  { key: "route", label: "Route" },
  { key: "register", label: "Register" },
];

/** Map a CompilationStatus to the stage that is currently executing. */
const statusToActiveStage: Partial<Record<CompilationStatus, CompilationStage>> = {
  DETECTING: "detect",
  EXTRACTING: "extract",
  ENHANCING: "enhance",
  VALIDATING_IR: "validate_ir",
  GENERATING: "generate",
  BUILDING: "build",
  DEPLOYING: "deploy",
  VALIDATING_RUNTIME: "validate_runtime",
  ROUTING: "route",
  REGISTERING: "register",
};

type StageState = "completed" | "active" | "failed" | "pending";

function getStageState(
  stageKey: CompilationStage,
  status: CompilationStatus,
  currentStage?: CompilationStage,
  failedStage?: CompilationStage,
): StageState {
  const stageIndex = STAGES.findIndex((s) => s.key === stageKey);
  const activeStage = statusToActiveStage[status] ?? currentStage;
  const activeIndex = activeStage
    ? STAGES.findIndex((s) => s.key === activeStage)
    : -1;

  if (failedStage === stageKey && (status === "FAILED" || status === "ROLLING_BACK" || status === "ROLLED_BACK")) {
    return "failed";
  }

  if (status === "PUBLISHED") return "completed";

  if (activeIndex >= 0) {
    if (stageIndex < activeIndex) return "completed";
    if (stageIndex === activeIndex) return "active";
  }

  return "pending";
}

function StageIcon({ state }: { state: StageState }) {
  switch (state) {
    case "completed":
      return <CheckCircle2 className="size-5 text-green-600 dark:text-green-400" />;
    case "active":
      return <Loader2 className="size-5 animate-spin text-blue-600 dark:text-blue-400" />;
    case "failed":
      return <XCircle className="size-5 text-red-600 dark:text-red-400" />;
    default:
      return <Circle className="size-5 text-muted-foreground/40" />;
  }
}

export function StageTimeline({
  status,
  currentStage,
  failedStage,
  selectedStage,
  onSelectStage,
}: {
  status: CompilationStatus;
  currentStage?: CompilationStage;
  failedStage?: CompilationStage;
  selectedStage?: CompilationStage;
  onSelectStage?: (stage: CompilationStage) => void;
}) {
  return (
    <div className="flex items-start gap-0 overflow-x-auto py-2">
      {STAGES.map((stage, i) => {
        const state = getStageState(stage.key, status, currentStage, failedStage);
        const isSelected = selectedStage === stage.key;
        return (
          <div key={stage.key} className="flex items-center">
            <button
              type="button"
              onClick={() => onSelectStage?.(stage.key)}
              className={cn(
                "flex flex-col items-center gap-1 rounded-lg px-2 py-2 transition-colors",
                "hover:bg-muted/60",
                isSelected && "bg-muted ring-1 ring-ring/20",
              )}
            >
              <StageIcon state={state} />
              <span
                className={cn(
                  "text-[11px] font-medium leading-tight whitespace-nowrap",
                  state === "completed" && "text-green-700 dark:text-green-400",
                  state === "active" && "text-blue-700 dark:text-blue-400",
                  state === "failed" && "text-red-700 dark:text-red-400",
                  state === "pending" && "text-muted-foreground/60",
                )}
              >
                {stage.label}
              </span>
            </button>
            {i < STAGES.length - 1 && (
              <div
                className={cn(
                  "mt-[-14px] h-0.5 w-4 shrink-0",
                  state === "completed"
                    ? "bg-green-400 dark:bg-green-600"
                    : "bg-border",
                )}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
