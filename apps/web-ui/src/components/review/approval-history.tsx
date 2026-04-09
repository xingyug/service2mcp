"use client";

import {
  Send,
  Eye,
  ThumbsUp,
  ThumbsDown,
  Upload,
  Rocket,
  RotateCcw,
  Clock,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { WorkflowHistoryEntry, WorkflowState } from "@/stores/workflow-store";

// ---------------------------------------------------------------------------
// State badge colors
// ---------------------------------------------------------------------------

const stateColors: Record<WorkflowState, string> = {
  draft: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  submitted: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  in_review: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
  approved: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  rejected: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  published: "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300",
  deployed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
};

const stateLabels: Record<WorkflowState, string> = {
  draft: "Draft",
  submitted: "Submitted",
  in_review: "In Review",
  approved: "Approved",
  rejected: "Rejected",
  published: "Published",
  deployed: "Deployed",
};

// ---------------------------------------------------------------------------
// Timeline icon per transition target
// ---------------------------------------------------------------------------

function transitionIcon(to: WorkflowState): { icon: LucideIcon; color: string } {
  switch (to) {
    case "submitted":
      return { icon: Send, color: "text-blue-500" };
    case "in_review":
      return { icon: Eye, color: "text-yellow-500" };
    case "approved":
      return { icon: ThumbsUp, color: "text-green-500" };
    case "rejected":
      return { icon: ThumbsDown, color: "text-red-500" };
    case "published":
      return { icon: Upload, color: "text-purple-500" };
    case "deployed":
      return { icon: Rocket, color: "text-emerald-500" };
    case "draft":
      return { icon: RotateCcw, color: "text-gray-500" };
    default:
      return { icon: Clock, color: "text-muted-foreground" };
  }
}

// ---------------------------------------------------------------------------
// Format
// ---------------------------------------------------------------------------

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ApprovalHistoryProps {
  history: WorkflowHistoryEntry[];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ApprovalHistory({ history }: ApprovalHistoryProps) {
  if (history.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        No workflow history yet. Submit the version for review to begin.
      </p>
    );
  }

  return (
    <ScrollArea className="max-h-[500px]">
      <div className="relative space-y-0 pl-6">
        {/* Vertical line */}
        <div className="absolute left-[11px] top-2 bottom-2 w-px bg-border" />

        {history.map((entry, idx) => {
          const { icon: Icon, color } = transitionIcon(entry.to);

          return (
            <div key={`${entry.timestamp}-${idx}`} className="relative pb-6 last:pb-0">
              {/* Dot / icon */}
              <div
                className={cn(
                  "absolute -left-6 flex size-6 items-center justify-center rounded-full border bg-background",
                  color,
                )}
              >
                <Icon className="size-3.5" />
              </div>

              {/* Content */}
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium">{entry.actor}</span>
                  <span className="text-muted-foreground">transitioned</span>
                  <Badge
                    variant="secondary"
                    className={cn("text-[10px]", stateColors[entry.from])}
                  >
                    {stateLabels[entry.from]}
                  </Badge>
                  <span className="text-muted-foreground">→</span>
                  <Badge
                    variant="secondary"
                    className={cn("text-[10px]", stateColors[entry.to])}
                  >
                    {stateLabels[entry.to]}
                  </Badge>
                </div>
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Clock className="size-3" />
                  {formatTimestamp(entry.timestamp)}
                </div>
                {entry.comment && (
                  <p className="text-sm text-muted-foreground italic">
                    &ldquo;{entry.comment}&rdquo;
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </ScrollArea>
  );
}
