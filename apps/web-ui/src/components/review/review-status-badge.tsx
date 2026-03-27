"use client";

import Link from "next/link";
import {
  Send,
  Eye,
  ThumbsUp,
  ThumbsDown,
  Upload,
  Rocket,
  FilePen,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { useWorkflowStore, type WorkflowState } from "@/stores/workflow-store";

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const badgeConfig: Record<
  WorkflowState,
  { label: string; icon: LucideIcon; color: string }
> = {
  draft: {
    label: "Draft",
    icon: FilePen,
    color: "bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700",
  },
  submitted: {
    label: "Submitted",
    icon: Send,
    color: "bg-blue-100 text-blue-700 hover:bg-blue-200 dark:bg-blue-900/40 dark:text-blue-300 dark:hover:bg-blue-900/60",
  },
  in_review: {
    label: "In Review",
    icon: Eye,
    color: "bg-yellow-100 text-yellow-700 hover:bg-yellow-200 dark:bg-yellow-900/40 dark:text-yellow-300 dark:hover:bg-yellow-900/60",
  },
  approved: {
    label: "Approved",
    icon: ThumbsUp,
    color: "bg-green-100 text-green-700 hover:bg-green-200 dark:bg-green-900/40 dark:text-green-300 dark:hover:bg-green-900/60",
  },
  rejected: {
    label: "Rejected",
    icon: ThumbsDown,
    color: "bg-red-100 text-red-700 hover:bg-red-200 dark:bg-red-900/40 dark:text-red-300 dark:hover:bg-red-900/60",
  },
  published: {
    label: "Published",
    icon: Upload,
    color: "bg-purple-100 text-purple-700 hover:bg-purple-200 dark:bg-purple-900/40 dark:text-purple-300 dark:hover:bg-purple-900/60",
  },
  deployed: {
    label: "Deployed",
    icon: Rocket,
    color: "bg-emerald-100 text-emerald-700 hover:bg-emerald-200 dark:bg-emerald-900/40 dark:text-emerald-300 dark:hover:bg-emerald-900/60",
  },
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ReviewStatusBadgeProps {
  serviceId: string;
  versionNumber: number;
  className?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ReviewStatusBadge({
  serviceId,
  versionNumber,
  className,
}: ReviewStatusBadgeProps) {
  const workflow = useWorkflowStore((s) => s.getWorkflow(serviceId, versionNumber));
  const state: WorkflowState = workflow?.state ?? "draft";
  const cfg = badgeConfig[state];
  const Icon = cfg.icon;

  return (
    <Link
      href={`/services/${serviceId}/review?version=${versionNumber}`}
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors",
        cfg.color,
        className,
      )}
    >
      <Icon className="size-3" />
      {cfg.label}
    </Link>
  );
}

// Standalone badge without link for inline display
export function ReviewStateBadge({
  state,
  className,
}: {
  state: WorkflowState;
  className?: string;
}) {
  const cfg = badgeConfig[state];
  const Icon = cfg.icon;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        cfg.color,
        className,
      )}
    >
      <Icon className="size-3" />
      {cfg.label}
    </span>
  );
}
