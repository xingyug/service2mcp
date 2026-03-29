"use client";

import * as React from "react";
import {
  Check,
  Send,
  Eye,
  ThumbsUp,
  ThumbsDown,
  Rocket,
  Upload,
  RotateCcw,
  Pencil,
} from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { useAuthStore } from "@/stores/auth-store";
import {
  useWorkflowStore,
  validTransitions,
  type WorkflowState,
} from "@/stores/workflow-store";
import { artifactApi, gatewayApi } from "@/lib/api-client";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const stateConfig: Record<
  WorkflowState,
  { label: string; color: string; ringColor: string; bgFill: string }
> = {
  draft: {
    label: "Draft",
    color: "border-gray-300 text-gray-500 dark:border-gray-600 dark:text-gray-400",
    ringColor: "ring-gray-400",
    bgFill: "bg-gray-500",
  },
  submitted: {
    label: "Submitted",
    color: "border-blue-400 text-blue-600 dark:border-blue-500 dark:text-blue-400",
    ringColor: "ring-blue-400",
    bgFill: "bg-blue-500",
  },
  in_review: {
    label: "In Review",
    color: "border-yellow-400 text-yellow-600 dark:border-yellow-500 dark:text-yellow-400",
    ringColor: "ring-yellow-400",
    bgFill: "bg-yellow-500",
  },
  approved: {
    label: "Approved",
    color: "border-green-400 text-green-600 dark:border-green-500 dark:text-green-400",
    ringColor: "ring-green-400",
    bgFill: "bg-green-500",
  },
  rejected: {
    label: "Rejected",
    color: "border-red-400 text-red-600 dark:border-red-500 dark:text-red-400",
    ringColor: "ring-red-400",
    bgFill: "bg-red-500",
  },
  published: {
    label: "Published",
    color: "border-purple-400 text-purple-600 dark:border-purple-500 dark:text-purple-400",
    ringColor: "ring-purple-400",
    bgFill: "bg-purple-500",
  },
  deployed: {
    label: "Deployed",
    color: "border-emerald-400 text-emerald-600 dark:border-emerald-500 dark:text-emerald-400",
    ringColor: "ring-emerald-400",
    bgFill: "bg-emerald-500",
  },
};

/** The "happy path" order for rendering the stepper. */
const happyPath: WorkflowState[] = [
  "draft",
  "submitted",
  "in_review",
  "approved",
  "published",
  "deployed",
];

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ApprovalWorkflowProps {
  serviceId: string;
  versionNumber: number;
  currentState: WorkflowState;
  onStateChange?: (newState: WorkflowState) => void;
  onEditIR?: () => void;
}

// ---------------------------------------------------------------------------
// Step node
// ---------------------------------------------------------------------------

function StepNode({
  state,
  currentState,
  isCompleted,
  isBranch,
}: {
  state: WorkflowState;
  currentState: WorkflowState;
  isCompleted: boolean;
  isBranch?: boolean;
}) {
  const cfg = stateConfig[state];
  const isCurrent = state === currentState;

  return (
    <div
      className={cn(
        "flex flex-col items-center gap-1",
        isBranch && "mt-2",
      )}
    >
      <div
        className={cn(
          "flex size-9 items-center justify-center rounded-full border-2 transition-all",
          cfg.color,
          isCurrent && `ring-2 ring-offset-2 ring-offset-background ${cfg.ringColor}`,
          isCompleted && `${cfg.bgFill} border-transparent text-white`,
        )}
      >
        {isCompleted ? (
          <Check className="size-4" />
        ) : (
          <span className="text-xs font-bold">
            {cfg.label.charAt(0)}
          </span>
        )}
      </div>
      <span
        className={cn(
          "text-[11px] font-medium",
          isCurrent ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {cfg.label}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Connector line
// ---------------------------------------------------------------------------

function Connector({ active }: { active: boolean }) {
  return (
    <div
      className={cn(
        "h-0.5 flex-1 min-w-6 max-w-12 mt-4",
        active ? "bg-primary" : "bg-border",
      )}
    />
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ApprovalWorkflow({
  serviceId,
  versionNumber,
  currentState,
  onStateChange,
  onEditIR,
}: ApprovalWorkflowProps) {
  const transition = useWorkflowStore((s) => s.transition);
  const user = useAuthStore((s) => s.user);

  const [confirmDialog, setConfirmDialog] = React.useState<{
    targetState: WorkflowState;
    label: string;
    description: string;
  } | null>(null);
  const [comment, setComment] = React.useState("");

  // Determine completed states along the happy path
  const currentIdx = happyPath.indexOf(currentState);
  const isRejected = currentState === "rejected";

  function completedSet(): Set<WorkflowState> {
    const set = new Set<WorkflowState>();
    if (isRejected) {
      // draft, submitted, in_review are "past" when rejected
      set.add("draft");
      set.add("submitted");
      set.add("in_review");
      return set;
    }
    for (let i = 0; i < currentIdx; i++) {
      set.add(happyPath[i]);
    }
    return set;
  }

  const completed = completedSet();

  const [submitting, setSubmitting] = React.useState(false);

  function requestTransition(to: WorkflowState, label: string, description: string) {
    setComment("");
    setConfirmDialog({ targetState: to, label, description });
  }

  async function executeTransition() {
    if (!confirmDialog) return;
    const actor = user?.username ?? "anonymous";
    setSubmitting(true);
    try {
      await transition(serviceId, versionNumber, confirmDialog.targetState, actor, comment || undefined);

      // Side-effects for publish / deploy
      if (confirmDialog.targetState === "published") {
        try {
          await artifactApi.activateVersion(serviceId, versionNumber);
        } catch {
          toast.error("Workflow transitioned to Published but artifact activation failed.");
        }
      }
      if (confirmDialog.targetState === "deployed") {
        try {
          await gatewayApi.syncRoutes({
            route_config: { service_id: serviceId, version_number: versionNumber },
          });
        } catch {
          toast.error("Workflow transitioned to Deployed but gateway sync failed.");
        }
      }

      onStateChange?.(confirmDialog.targetState);
      toast.success(`Workflow transitioned to "${stateConfig[confirmDialog.targetState].label}"`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Transition failed");
    } finally {
      setSubmitting(false);
      setConfirmDialog(null);
      setComment("");
    }
  }

  // ---------------------------------------------------------------------------
  // Action buttons
  // ---------------------------------------------------------------------------

  function renderActions() {
    const allowed = validTransitions[currentState];

    return (
      <div className="flex flex-wrap items-center gap-2">
        {currentState === "draft" && allowed.includes("submitted") && (
          <Button
            size="sm"
            onClick={() =>
              requestTransition(
                "submitted",
                "Submit for Review",
                "This will submit the current version for review. The IR will become read-only until a reviewer approves or rejects it.",
              )
            }
          >
            <Send className="mr-1.5 size-4" />
            Submit for Review
          </Button>
        )}

        {currentState === "submitted" && allowed.includes("in_review") && (
          <Button
            size="sm"
            onClick={() =>
              requestTransition(
                "in_review",
                "Start Review",
                "You are claiming this version for review. You will be able to approve or reject it.",
              )
            }
          >
            <Eye className="mr-1.5 size-4" />
            Start Review
          </Button>
        )}

        {currentState === "in_review" && (
          <>
            {allowed.includes("approved") && (
              <Button
                size="sm"
                className="bg-green-600 hover:bg-green-700"
                onClick={() =>
                  requestTransition(
                    "approved",
                    "Approve",
                    "Approving this version will allow it to be published.",
                  )
                }
              >
                <ThumbsUp className="mr-1.5 size-4" />
                Approve
              </Button>
            )}
            {allowed.includes("rejected") && (
              <Button
                size="sm"
                variant="destructive"
                onClick={() =>
                  requestTransition(
                    "rejected",
                    "Reject",
                    "Rejecting this version will send it back to draft for revisions. Please provide a reason.",
                  )
                }
              >
                <ThumbsDown className="mr-1.5 size-4" />
                Reject
              </Button>
            )}
            {onEditIR && (
              <Button size="sm" variant="outline" onClick={onEditIR}>
                <Pencil className="mr-1.5 size-4" />
                Edit IR
              </Button>
            )}
          </>
        )}

        {currentState === "approved" && allowed.includes("published") && (
          <Button
            size="sm"
            className="bg-purple-600 hover:bg-purple-700"
            onClick={() =>
              requestTransition(
                "published",
                "Publish",
                "Publishing makes this version available for deployment.",
              )
            }
          >
            <Upload className="mr-1.5 size-4" />
            Publish
          </Button>
        )}

        {currentState === "rejected" && allowed.includes("draft") && (
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              requestTransition(
                "draft",
                "Revise & Resubmit",
                "This will move the version back to draft so you can edit the IR and resubmit for review.",
              )
            }
          >
            <RotateCcw className="mr-1.5 size-4" />
            Revise &amp; Resubmit
          </Button>
        )}

        {currentState === "published" && allowed.includes("deployed") && (
          <Button
            size="sm"
            className="bg-emerald-600 hover:bg-emerald-700"
            onClick={() =>
              requestTransition(
                "deployed",
                "Deploy",
                "Deploying will make this version live in the gateway.",
              )
            }
          >
            <Rocket className="mr-1.5 size-4" />
            Deploy
          </Button>
        )}
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="space-y-4">
      {/* Visual flow */}
      <div className="flex items-start justify-center gap-1 overflow-x-auto py-2">
        {happyPath.map((state, i) => (
          <React.Fragment key={state}>
            <StepNode
              state={state}
              currentState={currentState}
              isCompleted={completed.has(state)}
            />
            {i < happyPath.length - 1 && (
              <Connector active={completed.has(happyPath[i + 1]) || happyPath[i + 1] === currentState} />
            )}
          </React.Fragment>
        ))}

        {/* Rejected branch shown when relevant */}
        {(isRejected || completed.has("rejected")) && (
          <>
            <div className="mx-2 mt-4 h-0.5 w-4 bg-red-300 dark:bg-red-700" />
            <StepNode
              state="rejected"
              currentState={currentState}
              isCompleted={!isRejected}
              isBranch
            />
          </>
        )}
      </div>

      {/* Actions */}
      {renderActions()}

      {/* Confirmation dialog */}
      <Dialog
        open={confirmDialog !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmDialog(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{confirmDialog?.label}</DialogTitle>
            <DialogDescription>{confirmDialog?.description}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <label htmlFor="transition-comment" className="text-sm font-medium">
              Comment (optional)
            </label>
            <Textarea
              id="transition-comment"
              placeholder="Add a reason or note…"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={3}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDialog(null)} disabled={submitting}>
              Cancel
            </Button>
            <Button onClick={executeTransition} disabled={submitting}>
              {submitting ? "Processing…" : confirmDialog?.label}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
