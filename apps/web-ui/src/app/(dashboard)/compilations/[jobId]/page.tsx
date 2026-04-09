"use client";

import { use, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  RotateCcw,
  Undo2,
  Clock,
  CheckCircle2,
  AlertTriangle,
  ExternalLink,
  Copy,
} from "lucide-react";
import { toast } from "sonner";

import {
  useCompilation,
  useRetryCompilation,
  useRollbackCompilation,
} from "@/hooks/use-api";
import { isCompilationInProgress } from "@/lib/compilation-status";
import { useCompilationEvents } from "@/lib/hooks/use-sse";
import { buildServiceDetailHref } from "@/lib/service-scope";
import type { CompilationStage } from "@/types/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/compilations/status-badge";
import { StageTimeline } from "@/components/compilations/stage-timeline";
import { EventLog } from "@/components/compilations/event-log";

// ── Helpers ─────────────────────────────────────────────────────────────────

function formatDate(iso?: string) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

function duration(created: string, completed?: string): string {
  const start = new Date(created).getTime();
  const end = completed ? new Date(completed).getTime() : Date.now();
  const secs = Math.floor((end - start) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ${secs % 60}s`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ${mins % 60}m`;
}

function copyToClipboard(text: string) {
  navigator.clipboard.writeText(text).then(
    () => toast.success("Copied to clipboard"),
    () => toast.error("Failed to copy"),
  );
}

// ── Component ───────────────────────────────────────────────────────────────

export default function CompilationDetailPage({
  params,
}: {
  params: Promise<{ jobId: string }>;
}) {
  const { jobId } = use(params);
  const router = useRouter();
  const [selectedStage, setSelectedStage] = useState<
    CompilationStage | undefined
  >();

  const { data: job, isLoading, error } = useCompilation(jobId, {
    refetchInterval: (query) =>
      isCompilationInProgress(query.state.data?.status) ? 5_000 : false,
  });

  const { events, isConnected, error: sseError } = useCompilationEvents(
    job && isCompilationInProgress(job.status) ? jobId : null,
  );

  const retryMutation = useRetryCompilation();
  const rollbackMutation = useRollbackCompilation();

  function handleRetry(fromStage?: string) {
    retryMutation.mutate(
      { jobId, fromStage },
      {
        onSuccess: (newJob) => {
          toast.success("Retry started");
          router.push(`/compilations/${newJob.job_id}`);
        },
        onError: () => toast.error("Failed to retry"),
      },
    );
  }

  function handleRollback() {
    rollbackMutation.mutate(jobId, {
      onSuccess: (newJob) => {
        toast.success("Rollback initiated");
        router.push(`/compilations/${newJob.job_id}`);
      },
      onError: () => toast.error("Failed to rollback"),
    });
  }

  // ── Loading state ──────────────────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full rounded-lg" />
        <Skeleton className="h-16 w-full rounded-lg" />
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/compilations" />}>
          <ArrowLeft data-icon="inline-start" />
          Back to Jobs
        </Button>
        <ErrorState
          title="Failed to load compilation job"
          message={
            error instanceof Error
              ? error.message
              : "The compilation detail request did not succeed."
          }
        />
      </div>
    );
  }

  if (!job) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/compilations" />}>
          <ArrowLeft data-icon="inline-start" />
          Back to Jobs
        </Button>
        <p className="text-muted-foreground">Compilation job not found.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ── Top Section ─────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <Button variant="ghost" size="sm" className="-ml-2 mb-1" nativeButton={false} render={<Link href="/compilations" />}>
            <ArrowLeft data-icon="inline-start" />
            Back to Jobs
          </Button>
          <div className="flex items-center gap-3">
            <h1 className="flex items-center gap-2 font-mono text-lg font-bold">
              <button
                type="button"
                onClick={() => copyToClipboard(job.job_id)}
                className="hover:text-primary"
                title="Copy full ID"
              >
                {job.job_id.slice(0, 8)}…
              </button>
              <Copy
                className="size-3.5 cursor-pointer text-muted-foreground hover:text-foreground"
                onClick={() => copyToClipboard(job.job_id)}
              />
            </h1>
            <StatusBadge status={job.status} />
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Clock className="size-3" />
              Created {formatDate(job.created_at)}
            </span>
            {job.completed_at && (
              <span className="flex items-center gap-1">
                <CheckCircle2 className="size-3" />
                Completed {formatDate(job.completed_at)}
              </span>
            )}
            <span>Duration: {duration(job.created_at, job.completed_at)}</span>
          </div>
        </div>

        <div className="flex gap-2">
          {job.status === "failed" && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => handleRetry(job.failed_stage)}
              disabled={retryMutation.isPending}
            >
              <RotateCcw data-icon="inline-start" />
              Retry{job.failed_stage ? ` from ${job.failed_stage}` : ""}
            </Button>
          )}
          {job.status === "succeeded" && (
            <Button
              size="sm"
              variant="destructive"
              onClick={handleRollback}
              disabled={rollbackMutation.isPending}
            >
              <Undo2 data-icon="inline-start" />
              Rollback
            </Button>
          )}
        </div>
      </div>

      <Separator />

      {/* ── Pipeline Stage Timeline ─────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle>Pipeline Stages</CardTitle>
        </CardHeader>
        <CardContent>
          <StageTimeline
            status={job.status}
            currentStage={job.current_stage}
            failedStage={job.failed_stage}
            selectedStage={selectedStage}
            onSelectStage={setSelectedStage}
          />
        </CardContent>
      </Card>

      {/* ── Events Panel ────────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            Events
            {selectedStage && (
              <span className="text-xs font-normal text-muted-foreground">
                — filtered to{" "}
                <button
                  type="button"
                  className="underline"
                  onClick={() => setSelectedStage(undefined)}
                >
                  {selectedStage} ✕
                </button>
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <EventLog
            events={events}
            isConnected={isConnected}
            error={sseError}
            filterStage={selectedStage}
          />
        </CardContent>
      </Card>

      {/* ── Error Section ───────────────────────────────────────────────── */}
      {job.status === "failed" && job.error_message && (
        <Card className="border-red-200 dark:border-red-900/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-red-600 dark:text-red-400">
              <AlertTriangle className="size-4" />
              Error
              {job.failed_stage && (
                <span className="text-xs font-normal text-muted-foreground">
                  — stage: {job.failed_stage}
                </span>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-red-50 p-3 text-xs text-red-800 dark:bg-red-950/30 dark:text-red-300">
              {job.error_message}
            </pre>
            <div className="mt-3">
              <Button
                size="sm"
                variant="outline"
                onClick={() => handleRetry(job.failed_stage)}
                disabled={retryMutation.isPending}
              >
                <RotateCcw data-icon="inline-start" />
                Retry from {job.failed_stage ?? "start"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* ── Artifacts Section ───────────────────────────────────────────── */}
      {job.status === "succeeded" && (
        <Card>
          <CardHeader>
            <CardTitle>Artifacts</CardTitle>
          </CardHeader>
          <CardContent>
            {job.artifacts ? (
            <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {job.artifacts.ir_id && (
                <div>
                  <dt className="text-xs font-medium text-muted-foreground">
                    IR ID
                  </dt>
                    <dd className="mt-0.5 font-mono text-xs">
                      <Link
                        href={buildServiceDetailHref(job.artifacts.ir_id, job)}
                        className={cn(
                          "inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline",
                        )}
                      >
                      {job.artifacts.ir_id.slice(0, 12)}…
                      <ExternalLink className="size-3" />
                    </Link>
                  </dd>
                </div>
              )}
              {job.artifacts.image_digest && (
                <div>
                  <dt className="text-xs font-medium text-muted-foreground">
                    Image Digest
                  </dt>
                  <dd className="mt-0.5 font-mono text-xs break-all">
                    {job.artifacts.image_digest}
                  </dd>
                </div>
              )}
              {job.artifacts.deployment_id && (
                <div>
                  <dt className="text-xs font-medium text-muted-foreground">
                    Deployment ID
                  </dt>
                  <dd className="mt-0.5 font-mono text-xs">
                    {job.artifacts.deployment_id}
                  </dd>
                </div>
              )}
            </dl>
            ) : (
              <p className="text-sm text-muted-foreground">
                No artifact information available for this compilation.
              </p>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
