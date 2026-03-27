"use client";

import * as React from "react";
import {
  Plus,
  Minus,
  ArrowRightLeft,
  FileWarning,
  ChevronDown,
  ChevronRight,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { RiskBadge } from "@/components/services/risk-badge";
import { useArtifactDiff, useArtifactVersions } from "@/hooks/use-api";
import type {
  Operation,
  ArtifactDiffChange,
  ArtifactDiffOperation,
} from "@/types/api";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface VersionDiffProps {
  serviceId: string;
  fromVersion: number;
  toVersion: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatValue(val: unknown): string {
  if (val === null || val === undefined) return "null";
  if (typeof val === "object") return JSON.stringify(val, null, 2);
  return String(val);
}

// ---------------------------------------------------------------------------
// Operation summary card
// ---------------------------------------------------------------------------

function OperationSummary({
  operation,
  variant,
}: {
  operation: Operation;
  variant: "added" | "removed";
}) {
  const [open, setOpen] = React.useState(false);

  return (
    <div className="rounded-md border bg-card">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/30 rounded-md"
      >
        {open ? (
          <ChevronDown className="size-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 text-muted-foreground" />
        )}
        <span className="text-sm font-semibold">{operation.name}</span>
        {operation.method && operation.path && (
          <span className="font-mono text-xs text-muted-foreground">
            {operation.method.toUpperCase()} {operation.path}
          </span>
        )}
        <RiskBadge level={operation.risk.risk_level} />
      </button>
      {open && (
        <div className="border-t px-3 py-2 text-xs space-y-1">
          <p className="text-muted-foreground">{operation.description}</p>
          {operation.params.length > 0 && (
            <div className="mt-1">
              <span className="font-semibold text-muted-foreground">
                Parameters:
              </span>
              <div className="mt-0.5 space-y-0.5">
                {operation.params.map((p) => (
                  <div key={p.name} className="flex items-center gap-2">
                    <code className="font-mono font-medium">{p.name}</code>
                    <Badge variant="secondary" className="h-4 text-[10px]">
                      {p.type}
                    </Badge>
                    {p.required && (
                      <Badge variant="outline" className="h-4 text-[10px]">
                        required
                      </Badge>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Change detail for a modified operation
// ---------------------------------------------------------------------------

function ChangeDetail({ change }: { change: ArtifactDiffChange }) {
  return (
    <div className="flex flex-col gap-1 rounded-md border bg-muted/30 px-3 py-2 text-xs">
      <span className="font-mono font-semibold text-muted-foreground">
        {change.field}
      </span>
      <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-4">
        {change.old_value !== undefined && (
          <div className="flex items-start gap-1">
            <Minus className="mt-0.5 size-3 shrink-0 text-red-500" />
            <pre className="whitespace-pre-wrap text-red-600 line-through dark:text-red-400">
              {formatValue(change.old_value)}
            </pre>
          </div>
        )}
        {change.new_value !== undefined && (
          <div className="flex items-start gap-1">
            <Plus className="mt-0.5 size-3 shrink-0 text-green-500" />
            <pre className="whitespace-pre-wrap text-green-600 dark:text-green-400">
              {formatValue(change.new_value)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Changed operation
// ---------------------------------------------------------------------------

function ChangedOperation({ op }: { op: ArtifactDiffOperation }) {
  const [open, setOpen] = React.useState(true);

  return (
    <div className="rounded-md border bg-card">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/30 rounded-md"
      >
        {open ? (
          <ChevronDown className="size-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 text-muted-foreground" />
        )}
        <ArrowRightLeft className="size-3.5 text-yellow-500" />
        <span className="text-sm font-semibold">{op.operation_id}</span>
        <Badge variant="secondary" className="h-4 text-[10px]">
          {op.changes?.length ?? 0} change
          {(op.changes?.length ?? 0) !== 1 ? "s" : ""}
        </Badge>
      </button>
      {open && op.changes && op.changes.length > 0 && (
        <div className="border-t px-3 py-2 space-y-2">
          {op.changes.map((change, idx) => (
            <ChangeDetail key={`${change.field}-${idx}`} change={change} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function DiffSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function VersionDiff({ serviceId, fromVersion, toVersion }: VersionDiffProps) {
  const [from, setFrom] = React.useState(fromVersion);
  const [to, setTo] = React.useState(toVersion);

  const { data: versionsData } = useArtifactVersions(serviceId);
  const versions = versionsData?.versions ?? [];

  const { data: diff, isLoading, error } = useArtifactDiff(serviceId, from, to);

  // Sync external prop changes
  React.useEffect(() => {
    setFrom(fromVersion);
  }, [fromVersion]);
  React.useEffect(() => {
    setTo(toVersion);
  }, [toVersion]);

  const addedCount = diff?.added_operations.length ?? 0;
  const removedCount = diff?.removed_operations.length ?? 0;
  const changedCount = diff?.changed_operations.length ?? 0;

  return (
    <div className="space-y-4">
      {/* Version selectors */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">From:</span>
          <Select
            value={String(from)}
            onValueChange={(v) => setFrom(Number(v))}
          >
            <SelectTrigger size="sm" className="w-24">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {versions.map((v) => (
                <SelectItem
                  key={v.version_number}
                  value={String(v.version_number)}
                >
                  v{v.version_number}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <ArrowRightLeft className="size-4 text-muted-foreground" />
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">To:</span>
          <Select
            value={String(to)}
            onValueChange={(v) => setTo(Number(v))}
          >
            <SelectTrigger size="sm" className="w-24">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {versions.map((v) => (
                <SelectItem
                  key={v.version_number}
                  value={String(v.version_number)}
                >
                  v{v.version_number}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <Separator />

      {/* Content */}
      {isLoading && <DiffSkeleton />}

      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load diff: {(error as Error).message}
        </div>
      )}

      {!isLoading && !error && from === to && (
        <p className="py-8 text-center text-muted-foreground">
          Select two different versions to compare.
        </p>
      )}

      {diff && (
        <>
          {/* Summary */}
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <Badge
              variant="secondary"
              className="bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300"
            >
              <Plus className="mr-1 size-3" />
              {addedCount} added
            </Badge>
            <Badge
              variant="secondary"
              className="bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300"
            >
              <Minus className="mr-1 size-3" />
              {removedCount} removed
            </Badge>
            <Badge
              variant="secondary"
              className="bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300"
            >
              <ArrowRightLeft className="mr-1 size-3" />
              {changedCount} changed
            </Badge>
          </div>

          {/* Empty state */}
          {addedCount === 0 && removedCount === 0 && changedCount === 0 && (
            <div className="flex flex-col items-center gap-2 py-12 text-center">
              <FileWarning className="size-8 text-muted-foreground" />
              <p className="text-muted-foreground">
                No differences between v{diff.from_version} and v
                {diff.to_version}.
              </p>
            </div>
          )}

          {/* Added */}
          {addedCount > 0 && (
            <Card className="border-green-200 dark:border-green-800">
              <CardHeader className="py-3 bg-green-50 dark:bg-green-900/20 rounded-t-lg">
                <CardTitle className="flex items-center gap-2 text-sm text-green-700 dark:text-green-300">
                  <Plus className="size-4" />
                  Added Operations ({addedCount})
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 pt-3">
                {diff.added_operations.map((op) => (
                  <OperationSummary
                    key={op.id}
                    operation={op}
                    variant="added"
                  />
                ))}
              </CardContent>
            </Card>
          )}

          {/* Removed */}
          {removedCount > 0 && (
            <Card className="border-red-200 dark:border-red-800">
              <CardHeader className="py-3 bg-red-50 dark:bg-red-900/20 rounded-t-lg">
                <CardTitle className="flex items-center gap-2 text-sm text-red-700 dark:text-red-300">
                  <Minus className="size-4" />
                  Removed Operations ({removedCount})
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 pt-3">
                {diff.removed_operations.map((op) => (
                  <OperationSummary
                    key={op.id}
                    operation={op}
                    variant="removed"
                  />
                ))}
              </CardContent>
            </Card>
          )}

          {/* Changed */}
          {changedCount > 0 && (
            <Card className="border-yellow-200 dark:border-yellow-800">
              <CardHeader className="py-3 bg-yellow-50 dark:bg-yellow-900/20 rounded-t-lg">
                <CardTitle className="flex items-center gap-2 text-sm text-yellow-700 dark:text-yellow-300">
                  <ArrowRightLeft className="size-4" />
                  Changed Operations ({changedCount})
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 pt-3">
                {diff.changed_operations.map((op) => (
                  <ChangedOperation key={op.operation_id} op={op} />
                ))}
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
