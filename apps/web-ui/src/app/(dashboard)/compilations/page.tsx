"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  Plus,
  RefreshCw,
  Search,
  MoreHorizontal,
  Eye,
  RotateCcw,
  Undo2,
  PackageOpen,
} from "lucide-react";
import { toast } from "sonner";

import { useCompilations, useRetryCompilation, useRollbackCompilation } from "@/hooks/use-api";
import {
  ALL_COMPILATION_STATUSES,
  formatCompilationStatus,
  IN_PROGRESS_COMPILATION_STATUSES,
} from "@/lib/compilation-status";
import type { CompilationJobResponse, CompilationStatus } from "@/types/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { StatusBadge } from "@/components/compilations/status-badge";

// ── Helpers ─────────────────────────────────────────────────────────────────

type DateRange = "24h" | "7d" | "30d" | "all";

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

function duration(created: string, completed?: string): string {
  const start = new Date(created).getTime();
  const end = completed ? new Date(completed).getTime() : Date.now();
  const secs = Math.floor((end - start) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const remSecs = secs % 60;
  if (mins < 60) return `${mins}m ${remSecs}s`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ${mins % 60}m`;
}

function truncateId(id: string) {
  return id.length > 8 ? id.slice(0, 8) + "…" : id;
}

const ITEMS_PER_PAGE = 20;

// ── Component ───────────────────────────────────────────────────────────────

export default function CompilationsPage() {
  const [statusFilter, setStatusFilter] = useState<CompilationStatus | "ALL">("ALL");
  const [search, setSearch] = useState("");
  const [dateRange, setDateRange] = useState<DateRange>("all");
  const [page, setPage] = useState(0);
  const [filterNow, setFilterNow] = useState(Date.now);

  const hasRunningJobs = (data?: CompilationJobResponse[]) =>
    data?.some((j) => IN_PROGRESS_COMPILATION_STATUSES.has(j.status));

  const { data: jobs, isLoading, refetch } = useCompilations({
    refetchInterval: (query) =>
      hasRunningJobs(query.state.data) ? 10_000 : false,
  });

  const retryMutation = useRetryCompilation();
  const rollbackMutation = useRollbackCompilation();

  const filtered = useMemo(() => {
    if (!jobs) return [];
    let result = jobs;

    if (statusFilter !== "ALL") {
      result = result.filter((j) => j.status === statusFilter);
    }

    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (j) =>
          j.job_id.toLowerCase().includes(q) ||
          (j.artifacts?.ir_id && j.artifacts.ir_id.toLowerCase().includes(q)),
      );
    }

    if (dateRange !== "all") {
      const ms =
        dateRange === "24h"
          ? 86_400_000
          : dateRange === "7d"
            ? 604_800_000
            : 2_592_000_000;
      result = result.filter(
        (j) => filterNow - new Date(j.created_at).getTime() < ms,
      );
    }

    return result;
  }, [jobs, statusFilter, search, dateRange, filterNow]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / ITEMS_PER_PAGE));
  const paged = filtered.slice(
    page * ITEMS_PER_PAGE,
    (page + 1) * ITEMS_PER_PAGE,
  );

  function handleRetry(job: CompilationJobResponse) {
    retryMutation.mutate(
      { jobId: job.job_id, fromStage: job.failed_stage },
      {
        onSuccess: () => toast.success("Retry started"),
        onError: () => toast.error("Failed to retry compilation"),
      },
    );
  }

  function handleRollback(job: CompilationJobResponse) {
    rollbackMutation.mutate(job.job_id, {
      onSuccess: () => toast.success("Rollback initiated"),
      onError: () => toast.error("Failed to rollback compilation"),
    });
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Compilation Jobs</h1>
          <p className="text-sm text-muted-foreground">
            View and manage tool compilation jobs.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => refetch()}
            aria-label="Refresh"
          >
            <RefreshCw className="size-3.5" />
          </Button>
          <Button render={<Link href="/compilations/new" />}>
            <Plus data-icon="inline-start" />
            New Compilation
          </Button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Status filter */}
        <DropdownMenu>
          <DropdownMenuTrigger
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded-lg border border-input bg-background px-2.5 text-sm",
              "hover:bg-muted dark:border-input dark:bg-input/30",
            )}
          >
            Status: {statusFilter === "ALL" ? "All" : formatCompilationStatus(statusFilter)}
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuItem onClick={() => { setStatusFilter("ALL"); setPage(0); }}>
              All
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {ALL_COMPILATION_STATUSES.map((s) => (
              <DropdownMenuItem key={s} onClick={() => { setStatusFilter(s); setPage(0); }}>
                {formatCompilationStatus(s)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Search */}
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search job ID…"
            className="h-8 w-56 pl-7"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(0); }}
          />
        </div>

        {/* Date range */}
        <div className="flex overflow-hidden rounded-lg border border-input">
          {(["24h", "7d", "30d", "all"] as const).map((range) => (
            <button
              key={range}
              type="button"
              onClick={() => { setDateRange(range); setPage(0); setFilterNow(Date.now()); }}
              className={cn(
                "px-2.5 py-1 text-xs font-medium transition-colors",
                dateRange === range
                  ? "bg-primary text-primary-foreground"
                  : "bg-background text-muted-foreground hover:bg-muted",
              )}
            >
              {range === "all" ? "All" : `Last ${range}`}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full rounded-lg" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-20 text-center">
          <PackageOpen className="size-12 text-muted-foreground/40" />
          <p className="text-lg font-medium text-muted-foreground">
            No compilation jobs found
          </p>
          <p className="text-sm text-muted-foreground/80">
            {search || statusFilter !== "ALL" || dateRange !== "all"
              ? "Try adjusting your filters."
              : "Start by creating a new compilation."}
          </p>
          {!search && statusFilter === "ALL" && dateRange === "all" && (
            <Button className="mt-2" render={<Link href="/compilations/new" />}>
              <Plus data-icon="inline-start" />
              New Compilation
            </Button>
          )}
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50 text-left text-xs font-medium text-muted-foreground">
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Job ID</th>
                  <th className="px-3 py-2">Stage</th>
                  <th className="px-3 py-2">Created</th>
                  <th className="px-3 py-2">Duration</th>
                  <th className="px-3 py-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {paged.map((job) => (
                  <tr
                    key={job.job_id}
                    className="border-b last:border-0 hover:bg-muted/30"
                  >
                    <td className="px-3 py-2">
                      <StatusBadge status={job.status} />
                    </td>
                    <td className="px-3 py-2">
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger
                            render={<Link href={`/compilations/${job.job_id}`} />}
                            className="font-mono text-xs text-primary underline-offset-4 hover:underline"
                          >
                            {truncateId(job.job_id)}
                          </TooltipTrigger>
                          <TooltipContent>{job.job_id}</TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </td>
                    <td className="px-3 py-2">
                      {job.current_stage ? (
                        <span className="flex items-center gap-1.5 text-xs">
                          {IN_PROGRESS_COMPILATION_STATUSES.has(job.status) && (
                            <span className="inline-block size-1.5 animate-pulse rounded-full bg-blue-500" />
                          )}
                          {job.current_stage}
                          {job.progress_pct != null && (
                            <span className="text-muted-foreground">
                              {job.progress_pct}%
                            </span>
                          )}
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger render={<span />} className="cursor-default text-xs">
                            {relativeTime(job.created_at)}
                          </TooltipTrigger>
                          <TooltipContent>
                            {new Date(job.created_at).toLocaleString()}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {!job.completed_at && IN_PROGRESS_COMPILATION_STATUSES.has(job.status) ? (
                        <span className="italic text-muted-foreground">
                          running
                        </span>
                      ) : (
                        duration(job.created_at, job.completed_at)
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <DropdownMenu>
                        <DropdownMenuTrigger
                          className="inline-flex size-7 items-center justify-center rounded-md hover:bg-muted"
                          aria-label="Actions"
                        >
                          <MoreHorizontal className="size-4" />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem render={<Link href={`/compilations/${job.job_id}`} />}>
                            <Eye className="mr-1.5 size-3.5" />
                            View
                          </DropdownMenuItem>
                          {job.status === "failed" && (
                            <DropdownMenuItem onClick={() => handleRetry(job)}>
                              <RotateCcw className="mr-1.5 size-3.5" />
                              Retry
                            </DropdownMenuItem>
                          )}
                          {job.status === "succeeded" && (
                            <DropdownMenuItem
                              variant="destructive"
                              onClick={() => handleRollback(job)}
                            >
                              <Undo2 className="mr-1.5 size-3.5" />
                              Rollback
                            </DropdownMenuItem>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>
                Showing {page * ITEMS_PER_PAGE + 1}–
                {Math.min((page + 1) * ITEMS_PER_PAGE, filtered.length)} of{" "}
                {filtered.length}
              </span>
              <div className="flex gap-1">
                <Button
                  variant="outline"
                  size="xs"
                  disabled={page === 0}
                  onClick={() => setPage((p) => p - 1)}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="xs"
                  disabled={page >= totalPages - 1}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
