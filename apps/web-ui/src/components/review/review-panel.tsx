"use client";

import * as React from "react";
import {
  CheckSquare,
  ChevronDown,
  ChevronRight,
  MessageSquare,
} from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { RiskBadge } from "@/components/services/risk-badge";
import type { ServiceIR, Operation, ServiceScope } from "@/types/api";
import { useWorkflowStore, type WorkflowRecord } from "@/stores/workflow-store";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ReviewPanelProps {
  ir: ServiceIR;
  serviceId: string;
  versionNumber: number;
  scope?: ServiceScope;
  workflow?: WorkflowRecord;
  readOnly?: boolean;
  onCompleteReview?: (notes: Record<string, string>, overallNote: string) => void;
}

// ---------------------------------------------------------------------------
// Operation review row
// ---------------------------------------------------------------------------

function OperationReviewRow({
  operation,
  reviewed,
  onToggle,
  note,
  onNoteChange,
  readOnly,
}: {
  operation: Operation;
  reviewed: boolean;
  onToggle: () => void;
  note: string;
  onNoteChange: (val: string) => void;
  readOnly?: boolean;
}) {
  const [expanded, setExpanded] = React.useState(false);
  const [showNote, setShowNote] = React.useState(false);

  return (
    <div
      className={cn(
        "rounded-md border transition-colors",
        reviewed
          ? "border-green-200 bg-green-50/50 dark:border-green-800 dark:bg-green-900/10"
          : "bg-card",
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-3 px-3 py-2">
        <Checkbox
          checked={reviewed}
          onCheckedChange={onToggle}
          disabled={readOnly}
          aria-label={`Mark ${operation.name} as reviewed`}
        />

        <button
          type="button"
          className="flex flex-1 items-center gap-2 text-left hover:opacity-80"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? (
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
        </button>

        <RiskBadge level={operation.risk.risk_level} />

        {operation.enabled ? (
          <Badge
            variant="secondary"
            className="bg-green-100 text-green-700 text-[10px] dark:bg-green-900/40 dark:text-green-300"
          >
            Enabled
          </Badge>
        ) : (
          <Badge variant="outline" className="text-[10px]">
            Disabled
          </Badge>
        )}

        <Button
          variant="ghost"
          size="icon-xs"
          className={cn(note && "text-blue-500")}
          onClick={() => setShowNote(!showNote)}
        >
          <MessageSquare className="size-3.5" />
        </Button>
      </div>

      {/* Expanded details */}
      {expanded && (
        <div className="border-t px-4 py-2 text-xs space-y-2">
          <p className="text-muted-foreground">{operation.description}</p>

          {operation.params.length > 0 && (
            <div>
              <span className="font-semibold text-muted-foreground">
                Parameters ({operation.params.length}):
              </span>
              <div className="mt-1 space-y-0.5">
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

          <div className="flex flex-wrap gap-3 text-muted-foreground">
            {operation.risk.writes_state != null && (
              <span>writes_state: {String(operation.risk.writes_state)}</span>
            )}
            {operation.risk.destructive != null && (
              <span>destructive: {String(operation.risk.destructive)}</span>
            )}
            {operation.risk.idempotent != null && (
              <span>idempotent: {String(operation.risk.idempotent)}</span>
            )}
          </div>
        </div>
      )}

      {/* Note area */}
      {showNote && (
        <div className="border-t px-3 py-2">
          <Textarea
            placeholder="Add review note for this operation…"
            value={note}
            onChange={(e) => onNoteChange(e.target.value)}
            rows={2}
            className="text-xs"
            readOnly={readOnly}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ReviewPanel({
  ir,
  serviceId,
  versionNumber,
  scope,
  workflow,
  readOnly,
  onCompleteReview,
}: ReviewPanelProps) {
  const saveNotes = useWorkflowStore((s) => s.saveNotes);
  const [reviewed, setReviewed] = React.useState<Set<string>>(new Set());
  const [notes, setNotes] = React.useState<Record<string, string>>({});
  const [overallNote, setOverallNote] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  const operations = ir.operations;
  const total = operations.length;
  const reviewedCount = reviewed.size;
  const progressPct = total > 0 ? Math.round((reviewedCount / total) * 100) : 0;
  const allReviewed = reviewedCount === total && total > 0;

  React.useEffect(() => {
    const savedNotes = workflow?.reviewNotes;
    const reviewedOperations = savedNotes?.reviewed_operations ?? [];
    setReviewed(
      new Set(
        reviewedOperations.filter((opId) =>
          operations.some((operation) => operation.id === opId),
        ),
      ),
    );
    setNotes(savedNotes?.operation_notes ?? {});
    setOverallNote(savedNotes?.overall_note ?? "");
  }, [operations, workflow?.reviewNotes]);

  function toggleReviewed(opId: string) {
    setReviewed((prev) => {
      const next = new Set(prev);
      if (next.has(opId)) next.delete(opId);
      else next.add(opId);
      return next;
    });
  }

  function setNote(opId: string, val: string) {
    setNotes((prev) => ({ ...prev, [opId]: val }));
  }

  async function handleComplete() {
    setSaving(true);
    try {
      await saveNotes(
        serviceId,
        versionNumber,
        notes,
        overallNote || undefined,
        [...reviewed],
        scope,
      );
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Failed to save review notes.",
      );
      return;
    } finally {
      setSaving(false);
    }
    onCompleteReview?.(notes, overallNote);
  }

  return (
    <div className="space-y-4">
      {/* Progress */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2">
            <CheckSquare className="size-4 text-muted-foreground" />
            <span className="font-medium">Review Progress</span>
          </div>
          <span className="text-muted-foreground">
            {reviewedCount} of {total} operations reviewed
          </span>
        </div>
        <Progress value={progressPct} className="h-2" />
      </div>

      <Separator />

      {/* Operation checklist */}
      <ScrollArea className="max-h-[520px]">
        <div className="space-y-2 pr-3">
          {operations.map((op) => (
            <OperationReviewRow
              key={op.id}
              operation={op}
              reviewed={reviewed.has(op.id)}
              onToggle={() => toggleReviewed(op.id)}
              note={notes[op.id] ?? ""}
              onNoteChange={(v) => setNote(op.id, v)}
              readOnly={readOnly}
            />
          ))}
        </div>
      </ScrollArea>

      {operations.length === 0 && (
        <p className="py-8 text-center text-sm text-muted-foreground">
          No operations to review.
        </p>
      )}

      <Separator />

      {/* Overall notes */}
      <div className="space-y-2">
        <label htmlFor="overall-note" className="text-sm font-medium">
          Overall Review Notes
        </label>
        <Textarea
          id="overall-note"
          placeholder="Summarize your review findings…"
          value={overallNote}
          onChange={(e) => setOverallNote(e.target.value)}
          rows={3}
          readOnly={readOnly}
        />
      </div>

      {/* Complete button */}
      {!readOnly && (
        <Button
          disabled={!allReviewed || saving}
          onClick={() => void handleComplete()}
          className="w-full"
        >
          <CheckSquare className="mr-1.5 size-4" />
          {saving
            ? "Saving…"
            : allReviewed
              ? "Complete Review"
              : `Review all operations to continue (${total - reviewedCount} remaining)`}
        </Button>
      )}
    </div>
  );
}
