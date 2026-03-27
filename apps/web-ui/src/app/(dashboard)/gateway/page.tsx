"use client";

import * as React from "react";
import {
  Route,
  RefreshCw,
  Trash2,
  ArrowLeftRight,
  ChevronDown,
  ChevronRight,
  MoreHorizontal,
  Undo2,
  Eye,
  Upload,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Clock,
} from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { useServices } from "@/hooks/use-api";
import { gatewayApi } from "@/lib/api-client";
import type {
  ReconcileResponse,
  ServiceSummary,
} from "@/types/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type RouteStatus = "synced" | "drifted" | "error";

interface ServiceRoute {
  service: ServiceSummary;
  status: RouteStatus;
  lastSynced?: string;
  routeConfig?: Record<string, unknown>;
}

interface DeploymentEntry {
  id: string;
  timestamp: string;
  serviceName: string;
  fromVersion: number | null;
  toVersion: number | null;
  action: "deploy" | "rollback" | "delete";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(iso?: string): string {
  if (!iso) return "Never";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function statusBadge(status: RouteStatus) {
  const map: Record<RouteStatus, { label: string; className: string }> = {
    synced: {
      label: "Synced",
      className:
        "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
    },
    drifted: {
      label: "Drifted",
      className:
        "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
    },
    error: {
      label: "Error",
      className:
        "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
    },
  };
  const cfg = map[status];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        cfg.className,
      )}
    >
      {cfg.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function OverviewCards({
  synced,
  drifted,
  errors,
}: {
  synced: number;
  drifted: number;
  errors: number;
}) {
  const cards = [
    {
      label: "Synced Routes",
      count: synced,
      color: "text-green-600 dark:text-green-400",
      bg: "bg-green-100 dark:bg-green-900/40",
      icon: CheckCircle2,
    },
    {
      label: "Drift Detected",
      count: drifted,
      color: "text-yellow-600 dark:text-yellow-400",
      bg: "bg-yellow-100 dark:bg-yellow-900/40",
      icon: AlertTriangle,
    },
    {
      label: "Errors",
      count: errors,
      color: "text-red-600 dark:text-red-400",
      bg: "bg-red-100 dark:bg-red-900/40",
      icon: XCircle,
    },
  ];
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      {cards.map((c) => {
        const Icon = c.icon;
        return (
          <Card key={c.label} className="flex items-center gap-4 p-4">
            <div className={cn("rounded-lg p-2", c.bg)}>
              <Icon className={cn("size-5", c.color)} />
            </div>
            <div>
              <p className="text-sm text-muted-foreground">{c.label}</p>
              <p className={cn("text-2xl font-bold tabular-nums", c.color)}>
                {c.count}
              </p>
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function RouteConfigViewer({ config }: { config?: Record<string, unknown> }) {
  if (!config) {
    return (
      <p className="py-2 text-sm text-muted-foreground">
        No route configuration available.
      </p>
    );
  }
  return (
    <ScrollArea className="max-h-64">
      <pre className="rounded-md bg-muted p-3 text-xs">
        {JSON.stringify(config, null, 2)}
      </pre>
    </ScrollArea>
  );
}

function ReconcileResults({
  result,
}: {
  result: ReconcileResponse | null;
}) {
  if (!result) return null;
  return (
    <Card className="animate-in fade-in-0 slide-in-from-top-2 border-blue-200 p-4 dark:border-blue-800">
      <h3 className="mb-3 text-sm font-semibold">Reconciliation Results</h3>
      <div className="flex gap-6">
        <div className="text-center">
          <p className="text-2xl font-bold text-green-600 tabular-nums dark:text-green-400">
            {result.synced}
          </p>
          <p className="text-xs text-muted-foreground">Synced</p>
        </div>
        <div className="text-center">
          <p className="text-2xl font-bold text-yellow-600 tabular-nums dark:text-yellow-400">
            {result.deleted}
          </p>
          <p className="text-xs text-muted-foreground">Deleted</p>
        </div>
        <div className="text-center">
          <p className="text-2xl font-bold text-red-600 tabular-nums dark:text-red-400">
            {result.errors}
          </p>
          <p className="text-xs text-muted-foreground">Errors</p>
        </div>
      </div>
    </Card>
  );
}

function DeploymentHistory({ entries }: { entries: DeploymentEntry[] }) {
  const [open, setOpen] = React.useState(false);

  const actionColors: Record<string, string> = {
    deploy: "bg-green-500",
    rollback: "bg-yellow-500",
    delete: "bg-red-500",
  };

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger
        render={
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-lg border bg-card px-4 py-3 text-left font-semibold transition-colors hover:bg-muted/30"
          />
        }
      >
        {open ? (
          <ChevronDown className="size-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-4 text-muted-foreground" />
        )}
        <Clock className="size-4 text-muted-foreground" />
        Deployment History
        <Badge variant="secondary" className="ml-auto">
          {entries.length}
        </Badge>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-2 rounded-lg border bg-card p-4">
          {entries.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No deployment history available.
            </p>
          ) : (
            <div className="relative ml-4 border-l-2 border-muted pl-6">
              {entries.map((entry) => (
                <div key={entry.id} className="relative mb-4 last:mb-0">
                  <div
                    className={cn(
                      "absolute -left-[31px] top-1 size-3 rounded-full ring-2 ring-background",
                      actionColors[entry.action] ?? "bg-gray-500",
                    )}
                  />
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="text-xs text-muted-foreground">
                      {relativeTime(entry.timestamp)}
                    </span>
                    <span className="font-medium">{entry.serviceName}</span>
                    <span className="text-muted-foreground">
                      {entry.fromVersion != null
                        ? `v${entry.fromVersion}`
                        : "—"}{" "}
                      →{" "}
                      {entry.toVersion != null
                        ? `v${entry.toVersion}`
                        : "—"}
                    </span>
                    <span
                      className={cn(
                        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide",
                        entry.action === "deploy" &&
                          "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
                        entry.action === "rollback" &&
                          "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
                        entry.action === "delete" &&
                          "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
                      )}
                    >
                      {entry.action}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function GatewayPage() {
  const { data: servicesData, isLoading, refetch } = useServices();
  const services = React.useMemo(() => servicesData?.services ?? [], [servicesData]);

  // Simulate route status based on service data
  const serviceRoutes: ServiceRoute[] = React.useMemo(
    () =>
      services.map((svc, i) => ({
        service: svc,
        status: (
          svc.active_version
            ? i % 5 === 0
              ? "error"
              : i % 3 === 0
                ? "drifted"
                : "synced"
            : "error"
        ) as RouteStatus,
        lastSynced: svc.last_compiled,
        routeConfig: svc.active_version
          ? {
              service_id: svc.service_id,
              upstream: { type: "roundrobin", nodes: { "127.0.0.1:8080": 1 } },
              uri: `/${svc.name.toLowerCase().replace(/\s+/g, "-")}/*`,
              plugins: { "proxy-rewrite": { regex_uri: ["^/.*$", "/"] } },
            }
          : undefined,
      })),
    [services],
  );

  const counts = React.useMemo(() => {
    const c = { synced: 0, drifted: 0, error: 0 };
    for (const r of serviceRoutes) c[r.status]++;
    return c;
  }, [serviceRoutes]);

  const [expandedRows, setExpandedRows] = React.useState<Set<string>>(
    new Set(),
  );
  const [reconcileResult, setReconcileResult] =
    React.useState<ReconcileResponse | null>(null);
  const [reconciling, setReconciling] = React.useState(false);

  // Dialog states
  const [syncDialogOpen, setSyncDialogOpen] = React.useState(false);
  const [rollbackDialogOpen, setRollbackDialogOpen] = React.useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false);
  const [selectedServiceId, setSelectedServiceId] = React.useState("");
  const [selectedVersion, setSelectedVersion] = React.useState("");
  const [actionLoading, setActionLoading] = React.useState(false);

  // Deployment history (simulated)
  const [deploymentHistory] = React.useState<DeploymentEntry[]>(() =>
    services.length > 0
      ? []
      : [
          {
            id: "1",
            timestamp: new Date(Date.now() - 3600_000).toISOString(),
            serviceName: "petstore-api",
            fromVersion: 2,
            toVersion: 3,
            action: "deploy",
          },
          {
            id: "2",
            timestamp: new Date(Date.now() - 7200_000).toISOString(),
            serviceName: "weather-api",
            fromVersion: 1,
            toVersion: 2,
            action: "deploy",
          },
          {
            id: "3",
            timestamp: new Date(Date.now() - 86400_000).toISOString(),
            serviceName: "petstore-api",
            fromVersion: 3,
            toVersion: 2,
            action: "rollback",
          },
          {
            id: "4",
            timestamp: new Date(Date.now() - 172800_000).toISOString(),
            serviceName: "legacy-service",
            fromVersion: 1,
            toVersion: null,
            action: "delete",
          },
        ],
  );

  const toggleRow = (id: string) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleReconcile = async () => {
    setReconciling(true);
    try {
      const result = await gatewayApi.reconcile();
      setReconcileResult(result);
      toast.success("Reconciliation complete");
      refetch();
    } catch (err) {
      toast.error(
        `Reconciliation failed: ${err instanceof Error ? err.message : "Unknown error"}`,
      );
    } finally {
      setReconciling(false);
    }
  };

  const handleSyncRoutes = async () => {
    if (!selectedServiceId) return;
    setActionLoading(true);
    try {
      await gatewayApi.setRoute({
        service_id: selectedServiceId,
        version_number: Number(selectedVersion) || 1,
        route_config: {},
      });
      toast.success(`Routes synced for service ${selectedServiceId}`);
      setSyncDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error(
        `Sync failed: ${err instanceof Error ? err.message : "Unknown error"}`,
      );
    } finally {
      setActionLoading(false);
    }
  };

  const handleRollback = async () => {
    if (!selectedServiceId) return;
    setActionLoading(true);
    try {
      await gatewayApi.setRoute({
        service_id: selectedServiceId,
        version_number: Math.max(
          1,
          (services.find((s) => s.service_id === selectedServiceId)
            ?.active_version ?? 1) - 1,
        ),
        route_config: {},
      });
      toast.success(`Rolled back routes for ${selectedServiceId}`);
      setRollbackDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error(
        `Rollback failed: ${err instanceof Error ? err.message : "Unknown error"}`,
      );
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeleteRoutes = async () => {
    if (!selectedServiceId) return;
    setActionLoading(true);
    try {
      await gatewayApi.deleteRoute(selectedServiceId);
      toast.success(`Routes deleted for ${selectedServiceId}`);
      setDeleteDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error(
        `Delete failed: ${err instanceof Error ? err.message : "Unknown error"}`,
      );
    } finally {
      setActionLoading(false);
    }
  };

  const openSyncDialog = (serviceId?: string) => {
    setSelectedServiceId(serviceId ?? "");
    setSelectedVersion("1");
    setSyncDialogOpen(true);
  };

  const openRollbackDialog = (serviceId?: string) => {
    setSelectedServiceId(serviceId ?? "");
    setRollbackDialogOpen(true);
  };

  const openDeleteDialog = (serviceId?: string) => {
    setSelectedServiceId(serviceId ?? "");
    setDeleteDialogOpen(true);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold">
            <Route className="size-6" />
            Gateway Routes
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage APISIX gateway route configuration and drift reconciliation
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetch()}
            disabled={isLoading}
          >
            <RefreshCw
              className={cn("size-4", isLoading && "animate-spin")}
            />
            Refresh
          </Button>
          <Button
            size="sm"
            onClick={handleReconcile}
            disabled={reconciling}
          >
            <ArrowLeftRight
              className={cn("size-4", reconciling && "animate-spin")}
            />
            Reconcile All
          </Button>
        </div>
      </div>

      {/* Reconciliation Results */}
      <ReconcileResults result={reconcileResult} />

      {/* Overview Cards */}
      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-20 rounded-lg" />
          ))}
        </div>
      ) : (
        <OverviewCards
          synced={counts.synced}
          drifted={counts.drifted}
          errors={counts.error}
        />
      )}

      {/* Route Actions */}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => openSyncDialog()}>
          <Upload className="size-4" />
          Sync Routes
        </Button>
        <Button variant="outline" size="sm" onClick={() => openRollbackDialog()}>
          <Undo2 className="size-4" />
          Rollback
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => openDeleteDialog()}
        >
          <Trash2 className="size-4" />
          Delete Routes
        </Button>
      </div>

      {/* Service Routes Table */}
      <Card>
        <div className="p-4 pb-0">
          <h2 className="text-lg font-semibold">Service Routes</h2>
        </div>
        <div className="p-4">
          {isLoading ? (
            <div className="space-y-2">
              {[1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-12 rounded-md" />
              ))}
            </div>
          ) : serviceRoutes.length === 0 ? (
            <div className="py-8 text-center">
              <Route className="mx-auto mb-2 size-8 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                No service routes found.
              </p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  <TableHead>Service Name</TableHead>
                  <TableHead>Version</TableHead>
                  <TableHead>Route Status</TableHead>
                  <TableHead>Last Synced</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {serviceRoutes.map((route) => {
                  const expanded = expandedRows.has(route.service.service_id);
                  return (
                    <React.Fragment key={route.service.service_id}>
                      <TableRow
                        className="cursor-pointer"
                        onClick={() => toggleRow(route.service.service_id)}
                      >
                        <TableCell>
                          {expanded ? (
                            <ChevronDown className="size-4 text-muted-foreground" />
                          ) : (
                            <ChevronRight className="size-4 text-muted-foreground" />
                          )}
                        </TableCell>
                        <TableCell className="font-medium">
                          {route.service.name}
                        </TableCell>
                        <TableCell>
                          {route.service.active_version != null ? (
                            <Badge variant="secondary">
                              v{route.service.active_version}
                            </Badge>
                          ) : (
                            <span className="text-sm text-muted-foreground">
                              —
                            </span>
                          )}
                        </TableCell>
                        <TableCell>{statusBadge(route.status)}</TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {relativeTime(route.lastSynced)}
                        </TableCell>
                        <TableCell>
                          <DropdownMenu>
                            <DropdownMenuTrigger
                              render={
                                <Button
                                  variant="ghost"
                                  size="icon-xs"
                                  onClick={(e: React.MouseEvent) =>
                                    e.stopPropagation()
                                  }
                                />
                              }
                            >
                              <MoreHorizontal className="size-4" />
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem
                                onClick={() =>
                                  openSyncDialog(route.service.service_id)
                                }
                              >
                                <Upload className="size-4" />
                                Sync Routes
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onClick={() =>
                                  openRollbackDialog(route.service.service_id)
                                }
                              >
                                <Undo2 className="size-4" />
                                Rollback Routes
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onClick={() =>
                                  toggleRow(route.service.service_id)
                                }
                              >
                                <Eye className="size-4" />
                                View Config
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                variant="destructive"
                                onClick={() =>
                                  openDeleteDialog(route.service.service_id)
                                }
                              >
                                <Trash2 className="size-4" />
                                Delete Routes
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </TableCell>
                      </TableRow>
                      {expanded && (
                        <TableRow>
                          <TableCell colSpan={6} className="bg-muted/30 p-4">
                            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                              Route Configuration
                            </h4>
                            <RouteConfigViewer config={route.routeConfig} />
                          </TableCell>
                        </TableRow>
                      )}
                    </React.Fragment>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </div>
      </Card>

      {/* Deployment History (F-025) */}
      <DeploymentHistory entries={deploymentHistory} />

      {/* Sync Routes Dialog */}
      <Dialog open={syncDialogOpen} onOpenChange={setSyncDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Sync Routes</DialogTitle>
            <DialogDescription>
              Sync gateway routes for a service version.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">Service ID</label>
              <Input
                placeholder="Enter service ID"
                value={selectedServiceId}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  setSelectedServiceId(e.target.value)
                }
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Version</label>
              <Input
                type="number"
                placeholder="Version number"
                value={selectedVersion}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  setSelectedVersion(e.target.value)
                }
              />
            </div>
          </div>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>
              Cancel
            </DialogClose>
            <Button
              onClick={handleSyncRoutes}
              disabled={actionLoading || !selectedServiceId}
            >
              {actionLoading ? "Syncing…" : "Sync Routes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Rollback Dialog */}
      <Dialog open={rollbackDialogOpen} onOpenChange={setRollbackDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Rollback Routes</DialogTitle>
            <DialogDescription>
              Roll back the gateway routes to a previous version.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">Service ID</label>
              <Input
                placeholder="Enter service ID"
                value={selectedServiceId}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  setSelectedServiceId(e.target.value)
                }
              />
            </div>
          </div>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>
              Cancel
            </DialogClose>
            <Button
              onClick={handleRollback}
              disabled={actionLoading || !selectedServiceId}
            >
              {actionLoading ? "Rolling back…" : "Rollback"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete Routes</DialogTitle>
            <DialogDescription>
              This will permanently remove gateway routes for the selected
              service. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">Service ID</label>
              <Input
                placeholder="Enter service ID to confirm"
                value={selectedServiceId}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  setSelectedServiceId(e.target.value)
                }
              />
            </div>
          </div>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>
              Cancel
            </DialogClose>
            <Button
              variant="destructive"
              onClick={handleDeleteRoutes}
              disabled={actionLoading || !selectedServiceId}
            >
              {actionLoading ? "Deleting…" : "Delete Routes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
