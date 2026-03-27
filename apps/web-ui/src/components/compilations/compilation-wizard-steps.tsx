"use client";

import { Fragment } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

interface WizardStepIndicatorProps {
  steps: string[];
  currentStep: number;
  onStepClick?: (step: number) => void;
}

export function WizardStepIndicator({
  steps,
  currentStep,
  onStepClick,
}: WizardStepIndicatorProps) {
  return (
    <nav aria-label="Progress" className="flex items-center justify-between">
      {steps.map((label, i) => {
        const isCompleted = i < currentStep;
        const isCurrent = i === currentStep;
        const isClickable = i <= currentStep;

        return (
          <Fragment key={label}>
            {i > 0 && (
              <div
                className={cn(
                  "mx-2 h-px flex-1",
                  isCompleted ? "bg-primary" : "bg-border",
                )}
              />
            )}
            <button
              type="button"
              onClick={() => isClickable && onStepClick?.(i)}
              className={cn(
                "group flex flex-col items-center gap-1.5",
                !isClickable && "cursor-default",
              )}
            >
              <div
                className={cn(
                  "flex h-8 w-8 items-center justify-center rounded-full text-sm font-medium transition-colors",
                  isCurrent && "bg-primary text-primary-foreground",
                  isCompleted && "bg-primary/10 text-primary",
                  !isCurrent &&
                    !isCompleted &&
                    "bg-muted text-muted-foreground",
                )}
              >
                {isCompleted ? <Check className="h-4 w-4" /> : i + 1}
              </div>
              <span
                className={cn(
                  "hidden text-xs sm:block",
                  isCurrent && "font-medium text-foreground",
                  isCompleted && "text-primary",
                  !isCurrent &&
                    !isCompleted &&
                    "text-muted-foreground",
                )}
              >
                {label}
              </span>
            </button>
          </Fragment>
        );
      })}
    </nav>
  );
}
