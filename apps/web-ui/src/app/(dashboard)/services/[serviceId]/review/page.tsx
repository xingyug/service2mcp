"use client";

import * as React from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, FileCheck } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApprovalWorkflow } from "@/components/review/approval-workflow";
import { ReviewPanel } from "@/components/review/review-panel";
import { ApprovalHistory } from "@/components/review/approval-history";
import { ReviewStateBadge } from "@/components/review/review-status-badge";
import { IREditor } from "@/components/services/ir-editor";
import { VersionDiff } from "@/components/services/version-diff";
import { ProtocolBadge } from "@/components/services/protocol-badge";
import { useService, useArtifactVersions, useUpdateIR } from "@/hooks/use-api";
import { useWorkflowStore, type WorkflowState } from "@/stores/workflow-store";

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ReviewPage() {
  const params = useParams<{ serviceId: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const serviceId = params.serviceId;

  const versionParam = searchParams.get("version");
  const requestedVersion = versionParam ? Number(versionParam) : undefined;

  const { data: service, isLoading: serviceLoading } = useService(serviceId);
  const { data: versionsData, isLoading: versionsLoading } = useArtifactVersions(serviceId);
  const versions = versionsData?.versions ?? [];

  const updateIR = useUpdateIR();

  // Determine which version to review
  const versionNumber = requestedVersion
    ?? service?.active_version
    ?? versions[0]?.version_number
    ?? 1;

  const versionData = versions.find((v) => v.version_number === versionNumber);
  const ir = versionData?.ir;

  // Previous version for diff
  const prevVersion = versions.find(
    (v) => v.version_number === versionNumber - 1,
  );

  // Workflow state
  const getOrCreate = useWorkflowStore((s) => s.getOrCreateWorkflow);
  const getWorkflow = useWorkflowStore((s) => s.getWorkflow);

  // Ensure workflow record exists
  React.useEffect(() => {
    getOrCreate(serviceId, versionNumber);
  }, [serviceId, versionNumber, getOrCreate]);

  const workflow = getWorkflow(serviceId, versionNumber);
  const currentState: WorkflowState = workflow?.state ?? "draft";
  const history = workflow?.history ?? [];

  // Tab state
  const [activeTab, setActiveTab] = React.useState("review");

  const isEditable = currentState === "draft" || currentState === "rejected";

  function handleEditIR() {
    setActiveTab("ir");
  }

  function handleCompleteReview(notes: Record<string, string>) {
    const noteCount = Object.values(notes).filter(Boolean).length;
    toast.success(
      `Review completed with ${noteCount} operation note${noteCount !== 1 ? "s" : ""}. You can now approve or reject.`,
    );
  }

  // Trigger re-render on state change
  const [, forceUpdate] = React.useReducer((x: number) => x + 1, 0);
  function handleStateChange() {
    forceUpdate();
  }

  const isLoading = serviceLoading || versionsLoading;

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (!service) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="mr-1 size-4" />
          Back
        </Button>
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Service not found.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="icon-xs" onClick={() => router.back()}>
              <ArrowLeft className="size-4" />
            </Button>
            <FileCheck className="size-5 text-muted-foreground" />
            <h1 className="text-2xl font-bold">Review &amp; Approval</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <span className="font-medium text-foreground">{service.name}</span>
            <ProtocolBadge protocol={service.protocol} />
            <Separator orientation="vertical" className="h-4" />
            <Badge variant="secondary">v{versionNumber}</Badge>
            <Separator orientation="vertical" className="h-4" />
            <ReviewStateBadge state={currentState} />
          </div>
        </div>
      </div>

      {/* Workflow stepper + actions */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Workflow</CardTitle>
        </CardHeader>
        <CardContent>
          <ApprovalWorkflow
            serviceId={serviceId}
            versionNumber={versionNumber}
            currentState={currentState}
            onStateChange={handleStateChange}
            onEditIR={handleEditIR}
          />
        </CardContent>
      </Card>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="review">Review</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
          <TabsTrigger value="ir">IR</TabsTrigger>
          {prevVersion && <TabsTrigger value="diff">Diff</TabsTrigger>}
        </TabsList>

        {/* Review tab */}
        <TabsContent value="review" className="mt-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">Operation Review Checklist</CardTitle>
            </CardHeader>
            <CardContent>
              {ir ? (
                <ReviewPanel
                  ir={ir}
                  readOnly={currentState !== "in_review"}
                  onCompleteReview={handleCompleteReview}
                />
              ) : (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  No IR available for this version.
                </p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* History tab */}
        <TabsContent value="history" className="mt-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">Approval History</CardTitle>
            </CardHeader>
            <CardContent>
              <ApprovalHistory history={history} />
            </CardContent>
          </Card>
        </TabsContent>

        {/* IR tab */}
        <TabsContent value="ir" className="mt-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">
                Intermediate Representation
                {isEditable && (
                  <Badge variant="outline" className="ml-2 text-[10px]">
                    Editable
                  </Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {ir ? (
                <IREditor
                  ir={ir}
                  readOnly={!isEditable}
                  onSave={(updatedIR) => {
                    updateIR.mutate(
                      { serviceId, versionNumber, irJson: updatedIR },
                      {
                        onSuccess: () => toast.success("IR saved successfully"),
                        onError: () => toast.error("Failed to save IR"),
                      },
                    );
                  }}
                />
              ) : (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  No IR available for this version.
                </p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Diff tab */}
        {prevVersion && (
          <TabsContent value="diff" className="mt-4">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm">
                  Version Diff — v{versionNumber - 1} → v{versionNumber}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <VersionDiff
                  serviceId={serviceId}
                  fromVersion={versionNumber - 1}
                  toVersion={versionNumber}
                />
              </CardContent>
            </Card>
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
