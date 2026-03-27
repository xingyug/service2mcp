"use client";

import { useMemo, useState } from "react";
import {
  ScrollText,
  Download,
  RefreshCw,
  Filter,
  X,
  ChevronRight,
} from "lucide-react";
import { toast } from "sonner";

import { useAuditLogs } from "@/hooks/use-api";
import { cn } from "@/lib/utils";
import type { AuditLogEntry } from "@/types/api";

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
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// ── Helpers ─────────────────────────────────────────────────────────────────

type DatePreset = "1h" | "24h" | "7d" | "30d" | "all";

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

function presetToIso(preset: DatePreset): string | undefined {
  if (preset === "all") return undefined;
  const ms: Record<string, number> = {
    "1h": 3_600_000,
    "24h": 86_400_000,
    "7d": 604_800_000,
    "30d": 2_592_000_000,
  };
  return new Date(Date.now() - ms[preset]).toISOString();
}

function truncate(str: string, max: number): string {
  return str.length > max ? str.slice(0, max) + "…" : str;
}

const ITEMS_PER_PAGE = 25;

const ACTION_COLORS: Record<string, string> = {
  create:
    "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  delete: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  update:
    "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  revoke:
    "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
};

function actionBadgeClass(action: string): string {
  const key = action.toLowerCase();
  for (const [prefix, cls] of Object.entries(ACTION_COLORS)) {
    if (key.startsWith(prefix)) return cls;
  }
  return "bg-muted text-muted-foreground";
}

function entriesToCSV(entries: AuditLogEntry[]): string {
  const headers = ["Timestamp", "Actor", "Action", "Resource", "Detail"];
  const rows = entries.map((e) => [
    e.timestamp,
    e.actor,
    e.action,
    e.resource,
    (e.detail ?? "").replace(/"/g, '""'),
  ]);
  return [
    headers.join(","),
    ...rows.map((r) => r.map((c) => `"${c}"`).join(",")),
  ].join("\n");
}

// ── Component ───────────────────────────────────────────────────────────────

export default function AuditLogPage() {
  // Filters
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [resource, setResource] = useState("");
  const [datePreset, setDatePreset] = useState<DatePreset>("24h");

  // Applied filters (only sent to API on "Apply")
  const [appliedFilters, setAppliedFilters] = useState<{
    actor?: string;
    action?: string;
    resource?: string;
    since?: string;
  }>({ since: presetToIso("24h") });

  // Pagination
  const [page, setPage] = useState(0);

  // Auto-refresh
  const [autoRefresh, setAutoRefresh] = useState(false);

  // Detail sheet
  const [selectedEntry, setSelectedEntry] = useState<AuditLogEntry | null>(null);

  const { data, isLoading } = useAuditLogs(
    appliedFilters,
    {
      refetchInterval: autoRefresh ? 30_000 : false,
    },
  );

  const entries = useMemo(() => {
    const list = data?.entries ?? [];
    // Sort by timestamp, newest first
    return [...list].sort(
      (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
    );
  }, [data]);

  const totalPages = Math.max(1, Math.ceil(entries.length / ITEMS_PER_PAGE));
  const paged = entries.slice(
    page * ITEMS_PER_PAGE,
    (page + 1) * ITEMS_PER_PAGE,
  );

  function applyFilters() {
    setAppliedFilters({
      actor: actor.trim() || undefined,
      action: action.trim() || undefined,
      resource: resource.trim() || undefined,
      since: presetToIso(datePreset),
    });
    setPage(0);
  }

  function clearFilters() {
    setActor("");
    setAction("");
    setResource("");
    setDatePreset("24h");
    setAppliedFilters({ since: presetToIso("24h") });
    setPage(0);
  }

  function exportCSV() {
    const csv = entriesToCSV(entries);
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(`Exported ${entries.length} entries`);
  }

  const hasActiveFilters =
    actor.trim() || action.trim() || resource.trim() || datePreset !== "24h";

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Audit Log</h1>
          <p className="text-sm text-muted-foreground">
            Review access control events and activity history.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Auto-refresh toggle */}
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant={autoRefresh ? "default" : "outline"}
                    size="icon-sm"
                    onClick={() => setAutoRefresh((v) => !v)}
                  />
                }
              >
                <RefreshCw
                  className={cn("size-3.5", autoRefresh && "animate-spin")}
                />
              </TooltipTrigger>
              <TooltipContent>
                {autoRefresh ? "Auto-refresh ON (30s)" : "Enable auto-refresh"}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>

          <Button variant="outline" size="sm" onClick={exportCSV} disabled={entries.length === 0}>
            <Download data-icon="inline-start" />
            Export CSV
          </Button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">Actor</Label>
          <Input
            placeholder="e.g. alice"
            className="h-8 w-36"
            value={actor}
            onChange={(e) => setActor(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">Action</Label>
          <Input
            placeholder="e.g. create"
            className="h-8 w-36"
            value={action}
            onChange={(e) => setAction(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">Resource</Label>
          <Input
            placeholder="e.g. policy-123"
            className="h-8 w-40"
            value={resource}
            onChange={(e) => setResource(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">Date Range</Label>
          <div className="flex overflow-hidden rounded-lg border border-input">
            {(["1h", "24h", "7d", "30d", "all"] as const).map((range) => (
              <button
                key={range}
                type="button"
                onClick={() => setDatePreset(range)}
                className={cn(
                  "px-2.5 py-1 text-xs font-medium transition-colors",
                  datePreset === range
                    ? "bg-primary text-primary-foreground"
                    : "bg-background text-muted-foreground hover:bg-muted",
                )}
              >
                {range === "all" ? "All" : `Last ${range}`}
              </button>
            ))}
          </div>
        </div>
        <Button size="sm" onClick={applyFilters}>
          <Filter data-icon="inline-start" />
          Apply
        </Button>
        {hasActiveFilters && (
          <Button variant="ghost" size="sm" onClick={clearFilters}>
            <X data-icon="inline-start" />
            Clear
          </Button>
        )}
      </div>

      {/* Detail Sheet */}
      <Sheet
        open={!!selectedEntry}
        onOpenChange={(open) => {
          if (!open) setSelectedEntry(null);
        }}
      >
        <SheetContent>
          <SheetHeader>
            <SheetTitle>Audit Entry Details</SheetTitle>
            <SheetDescription>
              Full details for this audit log entry.
            </SheetDescription>
          </SheetHeader>
          {selectedEntry && (
            <div className="space-y-4 p-4">
              <div className="grid gap-3">
                <div>
                  <Label className="text-xs text-muted-foreground">
                    Timestamp
                  </Label>
                  <p className="text-sm">
                    {new Date(selectedEntry.timestamp).toLocaleString()}
                  </p>
                </div>
                <div>
                  <Label className="text-xs text-muted-foreground">Actor</Label>
                  <p className="text-sm font-medium">{selectedEntry.actor}</p>
                </div>
                <div>
                  <Label className="text-xs text-muted-foreground">
                    Action
                  </Label>
                  <div className="mt-0.5">
                    <Badge className={actionBadgeClass(selectedEntry.action)}>
                      {selectedEntry.action}
                    </Badge>
                  </div>
                </div>
                <div>
                  <Label className="text-xs text-muted-foreground">
                    Resource
                  </Label>
                  <p className="text-sm font-mono">{selectedEntry.resource}</p>
                </div>
                {selectedEntry.detail && (
                  <div>
                    <Label className="text-xs text-muted-foreground">
                      Detail
                    </Label>
                    <pre className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border bg-muted/50 p-3 text-xs font-mono">
                      {selectedEntry.detail}
                    </pre>
                  </div>
                )}
                <div>
                  <Label className="text-xs text-muted-foreground">
                    Entry ID
                  </Label>
                  <p className="text-xs font-mono text-muted-foreground">
                    {selectedEntry.id}
                  </p>
                </div>
              </div>
            </div>
          )}
        </SheetContent>
      </Sheet>

      {/* Table */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full rounded-lg" />
          ))}
        </div>
      ) : entries.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-20 text-center">
          <ScrollText className="size-12 text-muted-foreground/40" />
          <p className="text-lg font-medium text-muted-foreground">
            No audit events found
          </p>
          <p className="text-sm text-muted-foreground/80">
            {hasActiveFilters
              ? "Try adjusting your filters or expanding the date range."
              : "Audit events will appear here as actions are performed."}
          </p>
        </div>
      ) : (
        <>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Timestamp</TableHead>
                  <TableHead>Actor</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Resource</TableHead>
                  <TableHead>Detail</TableHead>
                  <TableHead className="w-8" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {paged.map((entry) => (
                  <TableRow
                    key={entry.id}
                    className="cursor-pointer"
                    onClick={() => setSelectedEntry(entry)}
                  >
                    <TableCell>
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger
                            render={<span />}
                            className="cursor-default text-xs"
                          >
                            {relativeTime(entry.timestamp)}
                          </TooltipTrigger>
                          <TooltipContent>
                            {new Date(entry.timestamp).toLocaleString()}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </TableCell>
                    <TableCell>
                      <span className="text-sm font-medium">{entry.actor}</span>
                    </TableCell>
                    <TableCell>
                      <Badge className={actionBadgeClass(entry.action)}>
                        {entry.action}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <span className="text-sm font-mono">
                        {entry.resource}
                      </span>
                    </TableCell>
                    <TableCell>
                      {entry.detail ? (
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger
                              render={<span />}
                              className="cursor-default text-xs text-muted-foreground"
                            >
                              {truncate(entry.detail, 50)}
                            </TooltipTrigger>
                            <TooltipContent className="max-w-sm">
                              <p className="whitespace-pre-wrap text-xs">
                                {entry.detail}
                              </p>
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <ChevronRight className="size-3.5 text-muted-foreground" />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>
                Showing {page * ITEMS_PER_PAGE + 1}–
                {Math.min((page + 1) * ITEMS_PER_PAGE, entries.length)} of{" "}
                {entries.length}
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
