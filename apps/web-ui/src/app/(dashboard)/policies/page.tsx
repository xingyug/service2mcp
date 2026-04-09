"use client";

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import {
  Shield,
  Plus,
  Trash2,
  Pencil,
  Search,
  ChevronDown,
  ChevronUp,
  FlaskConical,
} from "lucide-react";
import { toast } from "sonner";

import { policyApi } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";
import { usePolicies } from "@/hooks/use-api";
import type {
  PolicyResponse,
  PolicyCreateRequest,
  SubjectType,
  PolicyDecision,
  RiskLevel,
  PolicyEvaluationRequest,
  PolicyEvaluationResponse,
} from "@/types/api";
import { RiskBadge } from "@/components/services/risk-badge";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// ── Helpers ─────────────────────────────────────────────────────────────────

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

const SUBJECT_TYPE_SUGGESTIONS: SubjectType[] = ["user", "group", "role"];
const SUBJECT_TYPE_PLACEHOLDER = "e.g. user, group, role";
const SUBJECT_TYPE_SUGGESTIONS_ID = "policy-subject-type-suggestions";
const RISK_LEVELS: RiskLevel[] = ["safe", "cautious", "dangerous", "unknown"];
const DECISIONS: PolicyDecision[] = ["allow", "deny", "require_approval"];

const decisionColors: Record<PolicyDecision, string> = {
  allow: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  deny: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  require_approval:
    "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
};

const decisionLabels: Record<PolicyDecision, string> = {
  allow: "Allow",
  deny: "Deny",
  require_approval: "Require Approval",
};

interface PolicyForm {
  subject_type: SubjectType;
  subject_id: string;
  resource_id: string;
  action_pattern: string;
  risk_threshold: RiskLevel;
  decision: PolicyDecision;
}

const emptyForm: PolicyForm = {
  subject_type: "user",
  subject_id: "",
  resource_id: "",
  action_pattern: "",
  risk_threshold: "safe",
  decision: "allow",
};

function evaluationSignature(request: PolicyEvaluationRequest): string {
  return JSON.stringify(request);
}

// ── Policy Evaluation Section (F-021) ───────────────────────────────────────

function PolicyEvalSection() {
  const [testOpen, setTestOpen] = useState(false);
  const [subjectType, setSubjectType] = useState<SubjectType>("user");
  const [subjectId, setSubjectId] = useState("");
  const [action, setAction] = useState("");
  const [resourceId, setResourceId] = useState("");
  const [riskLevel, setRiskLevel] = useState<RiskLevel>("safe");
  const [result, setResult] = useState<PolicyEvaluationResponse | null>(null);
  const [lastEvaluatedSignature, setLastEvaluatedSignature] = useState<string | null>(null);
  const evaluationRequest = useMemo<PolicyEvaluationRequest>(
    () => ({
      subject_type: subjectType,
      subject_id: subjectId,
      action,
      resource_id: resourceId,
      risk_level: riskLevel,
    }),
    [subjectType, subjectId, action, resourceId, riskLevel],
  );
  const visibleResult =
    lastEvaluatedSignature === evaluationSignature(evaluationRequest) ? result : null;

  const evalMutation = useMutation({
    mutationFn: (request: PolicyEvaluationRequest) => policyApi.evaluate(request),
    onMutate: (request) => {
      setResult(null);
      setLastEvaluatedSignature(evaluationSignature(request));
    },
    onSuccess: (res, request) => {
      setResult(res);
      setLastEvaluatedSignature(evaluationSignature(request));
    },
    onError: (_error, request) => {
      setResult(null);
      setLastEvaluatedSignature(evaluationSignature(request));
      toast.error("Policy evaluation failed");
    },
  });

  return (
    <Collapsible open={testOpen} onOpenChange={setTestOpen}>
      <Card>
        <CollapsibleTrigger className="w-full">
          <CardHeader className="flex flex-row items-center justify-between py-3">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <FlaskConical className="size-4" />
              Test Policy Evaluation
            </CardTitle>
            {testOpen ? (
              <ChevronUp className="size-4 text-muted-foreground" />
            ) : (
              <ChevronDown className="size-4 text-muted-foreground" />
            )}
          </CardHeader>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <CardContent className="space-y-4 pt-0">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <div className="space-y-1.5">
                <Label className="text-xs">Subject Type</Label>
                <Input
                  list={SUBJECT_TYPE_SUGGESTIONS_ID}
                  placeholder={SUBJECT_TYPE_PLACEHOLDER}
                  value={subjectType}
                  onChange={(e) => setSubjectType(e.target.value)}
                  className="h-8"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Subject ID</Label>
                <Input
                  placeholder="e.g. alice"
                  value={subjectId}
                  onChange={(e) => setSubjectId(e.target.value)}
                  className="h-8"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Action</Label>
                <Input
                  placeholder="e.g. read"
                  value={action}
                  onChange={(e) => setAction(e.target.value)}
                  className="h-8"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Resource ID</Label>
                <Input
                  placeholder="e.g. service-123"
                  value={resourceId}
                  onChange={(e) => setResourceId(e.target.value)}
                  className="h-8"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Risk Level</Label>
                <Select
                  value={riskLevel}
                  onValueChange={(value) => setRiskLevel(value as RiskLevel)}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {RISK_LEVELS.map((risk) => (
                      <SelectItem key={risk} value={risk}>
                        {risk}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <Button
                size="sm"
                onClick={() => evalMutation.mutate(evaluationRequest)}
                disabled={
                  !subjectType.trim() ||
                  !subjectId.trim() ||
                  !action.trim() ||
                  !resourceId.trim() ||
                  evalMutation.isPending
                }
              >
                {evalMutation.isPending ? "Evaluating…" : "Evaluate"}
              </Button>
              {visibleResult && (
                <div className="flex items-center gap-2 text-sm">
                  <span className="text-muted-foreground">Decision:</span>
                  <Badge className={decisionColors[visibleResult.decision]}>
                    {decisionLabels[visibleResult.decision]}
                  </Badge>
                  {visibleResult.matched_policy_id && (
                    <>
                      <span className="text-muted-foreground">Policy:</span>
                      <span className="font-mono text-xs text-primary">
                        {visibleResult.matched_policy_id}
                      </span>
                    </>
                  )}
                  {visibleResult.reason && (
                    <>
                      <span className="text-muted-foreground">Reason:</span>
                      <span className="text-xs">{visibleResult.reason}</span>
                    </>
                  )}
                </div>
              )}
            </div>
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  );
}

// ── Main Component ──────────────────────────────────────────────────────────

export default function PoliciesPage() {
  const searchParams = useSearchParams();
  const initialResourceId = searchParams.get("resource_id") ?? "";
  const queryClient = useQueryClient();

  // Filters
  const [filterSubjectType, setFilterSubjectType] = useState("");
  const [filterSubjectId, setFilterSubjectId] = useState("");
  const [filterResourceId, setFilterResourceId] = useState(initialResourceId);

  // Form dialog
  const [formOpen, setFormOpen] = useState(false);
  const [editingPolicy, setEditingPolicy] = useState<PolicyResponse | null>(null);
  const [form, setForm] = useState<PolicyForm>(emptyForm);

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<PolicyResponse | null>(null);

  const apiFilters = useMemo(() => {
    const f: { subject_type?: string; subject_id?: string; resource_id?: string } = {};
    if (filterSubjectType.trim()) f.subject_type = filterSubjectType.trim();
    if (filterSubjectId.trim()) f.subject_id = filterSubjectId.trim();
    if (filterResourceId.trim()) f.resource_id = filterResourceId.trim();
    return Object.keys(f).length > 0 ? f : undefined;
  }, [filterSubjectType, filterSubjectId, filterResourceId]);

  const { data, isLoading, error, refetch } = usePolicies(apiFilters);
  const policies = data?.policies ?? [];

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.policies.all });
    // Also invalidate any filtered queries
    queryClient.invalidateQueries({
      predicate: (q) => q.queryKey[0] === "policies",
    });
  };

  const createMutation = useMutation({
    mutationFn: (req: PolicyCreateRequest) => policyApi.create(req),
    onSuccess: () => {
      invalidate();
      closeForm();
      toast.success("Policy created");
    },
    onError: () => toast.error("Failed to create policy"),
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      req,
    }: {
      id: string;
      req: { action_pattern?: string; risk_threshold?: RiskLevel; decision?: PolicyDecision };
    }) => policyApi.update(id, req),
    onSuccess: () => {
      invalidate();
      closeForm();
      toast.success("Policy updated");
    },
    onError: () => toast.error("Failed to update policy"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => policyApi.delete(id),
    onSuccess: () => {
      invalidate();
      setDeleteTarget(null);
      toast.success("Policy deleted");
    },
    onError: () => toast.error("Failed to delete policy"),
  });

  function openCreate() {
    setEditingPolicy(null);
    setForm(emptyForm);
    setFormOpen(true);
  }

  function openEdit(policy: PolicyResponse) {
    setEditingPolicy(policy);
    setForm({
      subject_type: policy.subject_type,
      subject_id: policy.subject_id,
      resource_id: policy.resource_id,
      action_pattern: policy.action_pattern,
      risk_threshold: policy.risk_threshold,
      decision: policy.decision,
    });
    setFormOpen(true);
  }

  function closeForm() {
    setFormOpen(false);
    setEditingPolicy(null);
    setForm(emptyForm);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (editingPolicy) {
      updateMutation.mutate({
        id: editingPolicy.policy_id,
        req: {
          action_pattern: form.action_pattern,
          risk_threshold: form.risk_threshold,
          decision: form.decision,
        },
      });
    } else {
      createMutation.mutate(form);
    }
  }

  const isFormValid =
    form.subject_type.trim() &&
    form.subject_id.trim() &&
    form.resource_id.trim() &&
    form.action_pattern.trim();

  const isSaving = createMutation.isPending || updateMutation.isPending;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Authorization Policies</h1>
          <p className="text-sm text-muted-foreground">
            Define access control rules for subjects, resources, and actions.
          </p>
        </div>
        <Button onClick={openCreate}>
          <Plus data-icon="inline-start" />
          Create Policy
        </Button>
      </div>

      {/* Policy Evaluation (F-021) */}
      <PolicyEvalSection />

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <Input
          list={SUBJECT_TYPE_SUGGESTIONS_ID}
          placeholder="Any subject type"
          className="h-8 w-40"
          value={filterSubjectType}
          onChange={(e) => setFilterSubjectType(e.target.value)}
        />

        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Subject ID…"
            className="h-8 w-40 pl-7"
            value={filterSubjectId}
            onChange={(e) => setFilterSubjectId(e.target.value)}
          />
        </div>

        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Resource ID…"
            className="h-8 w-40 pl-7"
            value={filterResourceId}
            onChange={(e) => setFilterResourceId(e.target.value)}
          />
        </div>
      </div>

      {/* Create / Edit Dialog */}
      <Dialog open={formOpen} onOpenChange={(open) => { if (!open) closeForm(); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {editingPolicy ? "Edit Policy" : "Create Policy"}
            </DialogTitle>
            <DialogDescription>
              {editingPolicy
                ? "Update the action pattern, risk threshold, or decision."
                : "Define a new access control policy."}
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs">Subject Type</Label>
                <Input
                  list={SUBJECT_TYPE_SUGGESTIONS_ID}
                  placeholder={SUBJECT_TYPE_PLACEHOLDER}
                  value={form.subject_type}
                  onChange={(e) =>
                    setForm((current) => ({
                      ...current,
                      subject_type: e.target.value,
                    }))
                  }
                  disabled={!!editingPolicy}
                  required
                  className="h-8"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Subject ID</Label>
                <Input
                  placeholder="e.g. alice"
                  value={form.subject_id}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, subject_id: e.target.value }))
                  }
                  disabled={!!editingPolicy}
                  required
                  className="h-8"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Resource ID</Label>
                <div className="flex gap-1">
                  <Input
                    placeholder='e.g. svc-123 or "*"'
                    value={form.resource_id}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, resource_id: e.target.value }))
                    }
                    disabled={!!editingPolicy}
                    required
                    className="h-8"
                  />
                  {!editingPolicy && (
                    <Button
                      type="button"
                      variant="outline"
                      size="icon-sm"
                      onClick={() =>
                        setForm((f) => ({ ...f, resource_id: "*" }))
                      }
                      title="All Resources"
                    >
                      *
                    </Button>
                  )}
                </div>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Action Pattern</Label>
                <div className="flex gap-1">
                  <Input
                    placeholder='e.g. read or "*"'
                    value={form.action_pattern}
                    onChange={(e) =>
                      setForm((f) => ({
                        ...f,
                        action_pattern: e.target.value,
                      }))
                    }
                    required
                    className="h-8"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="icon-sm"
                    onClick={() =>
                      setForm((f) => ({ ...f, action_pattern: "*" }))
                    }
                    title="All Actions"
                  >
                    *
                  </Button>
                </div>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Risk Threshold</Label>
                <Select
                  value={form.risk_threshold}
                  onValueChange={(v) =>
                    setForm((f) => ({
                      ...f,
                      risk_threshold: v as RiskLevel,
                    }))
                  }
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {RISK_LEVELS.map((r) => (
                      <SelectItem key={r} value={r}>
                        {r}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Decision</Label>
                <Select
                  value={form.decision}
                  onValueChange={(v) =>
                    setForm((f) => ({
                      ...f,
                      decision: v as PolicyDecision,
                    }))
                  }
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {DECISIONS.map((d) => (
                      <SelectItem key={d} value={d}>
                        {decisionLabels[d]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <DialogFooter>
              <DialogClose render={<Button variant="outline" />}>
                Cancel
              </DialogClose>
              <Button type="submit" disabled={!isFormValid || isSaving}>
                {isSaving
                  ? "Saving…"
                  : editingPolicy
                    ? "Update Policy"
                    : "Create Policy"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Policy</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete this policy for &ldquo;
              {deleteTarget?.subject_id}&rdquo; on resource &ldquo;
              {deleteTarget?.resource_id}&rdquo;? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={() => {
                if (deleteTarget) deleteMutation.mutate(deleteTarget.policy_id);
              }}
            >
              {deleteMutation.isPending ? "Deleting…" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Table */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full rounded-lg" />
          ))}
        </div>
      ) : error ? (
        <ErrorState
          title="Failed to load policies"
          message={
            error instanceof Error
              ? error.message
              : "The policies request did not succeed."
          }
          onAction={() => {
            void refetch();
          }}
        />
      ) : policies.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-20 text-center">
          <Shield className="size-12 text-muted-foreground/40" />
          <p className="text-lg font-medium text-muted-foreground">
            No policies found
          </p>
          <p className="text-sm text-muted-foreground/80">
            {filterSubjectId || filterResourceId || filterSubjectType.trim()
              ? "Try adjusting your filters."
              : "Create a policy to define access control rules."}
          </p>
          {!filterSubjectId &&
            !filterResourceId &&
            !filterSubjectType.trim() && (
              <Button className="mt-2" onClick={openCreate}>
                <Plus data-icon="inline-start" />
                Create Policy
              </Button>
            )}
        </div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Subject</TableHead>
                <TableHead>Resource</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Risk Threshold</TableHead>
                <TableHead>Decision</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {policies.map((policy) => (
                <TableRow key={policy.policy_id}>
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      <Badge variant="outline" className="text-xs">
                        {policy.subject_type}
                      </Badge>
                      <span className="text-sm">{policy.subject_id}</span>
                    </div>
                  </TableCell>
                  <TableCell>
                    {policy.resource_id === "*" ? (
                      <Badge variant="secondary">All Resources</Badge>
                    ) : (
                      <span className="text-sm font-mono">
                        {policy.resource_id}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    {policy.action_pattern === "*" ? (
                      <Badge variant="secondary">All Actions</Badge>
                    ) : (
                      <span className="text-sm font-mono">
                        {policy.action_pattern}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <RiskBadge level={policy.risk_threshold} />
                  </TableCell>
                  <TableCell>
                    <Badge className={decisionColors[policy.decision]}>
                      {decisionLabels[policy.decision]}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger
                          render={<span />}
                          className="cursor-default text-xs"
                        >
                          {relativeTime(policy.created_at)}
                        </TooltipTrigger>
                        <TooltipContent>
                          {new Date(policy.created_at).toLocaleString()}
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        onClick={() => openEdit(policy)}
                        title="Edit"
                      >
                        <Pencil className="size-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        onClick={() => setDeleteTarget(policy)}
                        title="Delete"
                        className="text-destructive hover:text-destructive"
                      >
                        <Trash2 className="size-3" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <datalist id={SUBJECT_TYPE_SUGGESTIONS_ID}>
        {SUBJECT_TYPE_SUGGESTIONS.map((subjectType) => (
          <option key={subjectType} value={subjectType} />
        ))}
      </datalist>
    </div>
  );
}
