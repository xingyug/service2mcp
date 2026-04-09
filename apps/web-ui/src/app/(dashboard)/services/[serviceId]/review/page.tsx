"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, FileCheck } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
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
import { useService, useArtifactVersions } from "@/hooks/use-api";
import { artifactApi } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";
import { serviceScopeFromSearchParams } from "@/lib/service-scope";
import { useWorkflowStore, type WorkflowState } from "@/stores/workflow-store";

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ReviewPage() {
  const params = useParams<{ serviceId: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const serviceId = params.serviceId;
  const scope = serviceScopeFromSearchParams(searchParams);

  const versionParam = searchParams.get("version");
  const requestedVersion = React.useMemo(() => {
    if (versionParam == null) {
      return undefined;
    }
    const parsed = Number(versionParam);
    if (!Number.isInteger(parsed) || parsed < 1) {
      return null;
    }
    return parsed;
  }, [versionParam]);
  const invalidRequestedVersion = versionParam !== null && requestedVersion === null;

  const {
    data: service,
    isLoading: serviceLoading,
    error: serviceError,
  } = useService(serviceId, scope);
  const {
    data: versionsData,
    isLoading: versionsLoading,
    error: versionsError,
  } = useArtifactVersions(
    serviceId,
    scope,
  );
  const versions = versionsData?.versions ?? [];

  // Determine which version to review
  const versionNumber = requestedVersion
    ?? service?.active_version
    ?? versions[0]?.version_number
    ?? 1;

  const versionData = versions.find((v) => v.version_number === versionNumber);
  const ir = versionData?.ir;
  const missingRequestedVersion =
    !invalidRequestedVersion &&
    !!versions.length &&
    versionData == null;

  // Previous version for diff
  const prevVersion = [...versions]
    .filter((version) => version.version_number < versionNumber)
    .sort((left, right) => right.version_number - left.version_number)[0];

  // Workflow state — load from backend
  const loadWorkflow = useWorkflowStore((s) => s.loadWorkflow);
  const getWorkflow = useWorkflowStore((s) => s.getWorkflow);

  React.useEffect(() => {
    if (
      invalidRequestedVersion ||
      missingRequestedVersion ||
      versionsError ||
      !versionData
    ) {
      return;
    }
    void loadWorkflow(serviceId, versionNumber, scope);
  }, [
    invalidRequestedVersion,
    loadWorkflow,
    missingRequestedVersion,
    scope,
    serviceId,
    versionData,
    versionNumber,
    versionsError,
  ]);

  const workflow = getWorkflow(serviceId, versionNumber, scope);
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

  async function handleSaveIR(updatedIR: NonNullable<typeof ir>) {
    try {
      await artifactApi.updateVersion(
        serviceId,
        versionNumber,
        { ir_json: updatedIR },
        scope,
      );
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.artifacts.versions(serviceId, scope),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.artifacts.version(serviceId, versionNumber, scope),
        }),
      ]);
      toast.success("IR updated successfully.");
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to save updated IR.";
      toast.error(message);
      throw new Error(message);
    }
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

  if (serviceError) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="mr-1 size-4" />
          Back
        </Button>
        <ErrorState
          title="Failed to load service"
          message={
            serviceError instanceof Error
              ? serviceError.message
              : "The service detail request did not succeed."
          }
        />
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
        <ErrorState title="Service not found" message="Service not found." />
      </div>
    );
  }

  if (invalidRequestedVersion) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="mr-1 size-4" />
          Back
        </Button>
        <ErrorState
          title="Invalid review version"
          message="Choose a positive integer version number."
        />
      </div>
    );
  }

  if (versionsError) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="mr-1 size-4" />
          Back
        </Button>
        <ErrorState
          title="Failed to load artifact versions"
          message={
            versionsError instanceof Error
              ? versionsError.message
              : "The artifact versions request did not succeed."
          }
        />
      </div>
    );
  }

  if (missingRequestedVersion) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="mr-1 size-4" />
          Back
        </Button>
        <ErrorState
          title="Review version not found"
          message={`Review version v${versionNumber} was not found for this service.`}
        />
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
            scope={scope}
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
                  serviceId={serviceId}
                  versionNumber={versionNumber}
                  scope={scope}
                  workflow={workflow}
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
                    onSave={isEditable ? handleSaveIR : undefined}
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
                  Version Diff — v{prevVersion.version_number} → v{versionNumber}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <VersionDiff
                  serviceId={serviceId}
                  scope={scope}
                  fromVersion={prevVersion.version_number}
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
