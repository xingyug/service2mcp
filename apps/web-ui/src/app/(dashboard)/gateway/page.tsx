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
import { ErrorState } from "@/components/ui/error-state";
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
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { useServices } from "@/hooks/use-api";
import { artifactApi, gatewayApi } from "@/lib/api-client";
import {
  buildPreviousRoutes,
  findActiveArtifactVersion,
  findArtifactVersion,
  inferRouteStatus,
} from "@/lib/gateway-route-config";
import type {
  ArtifactVersionResponse,
  GatewayRouteDocument,
  GatewayPreviousRoutes,
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
  artifactTimestamp?: string;
  routeConfig?: Record<string, unknown>;
  versionLoadError?: string;
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

function RouteConfigViewer({
  config,
  errorMessage,
}: {
  config?: Record<string, unknown>;
  errorMessage?: string;
}) {
  if (errorMessage) {
    return (
      <p className="py-2 text-sm text-destructive">
        {errorMessage}
      </p>
    );
  }
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
  const stats = [
    {
      label: "Consumers Synced",
      count: result.consumers_synced,
      className: "text-green-600 dark:text-green-400",
    },
    {
      label: "Consumers Deleted",
      count: result.consumers_deleted,
      className: "text-yellow-600 dark:text-yellow-400",
    },
    {
      label: "Policies Synced",
      count: result.policy_bindings_synced,
      className: "text-green-600 dark:text-green-400",
    },
    {
      label: "Policies Deleted",
      count: result.policy_bindings_deleted,
      className: "text-yellow-600 dark:text-yellow-400",
    },
    {
      label: "Routes Synced",
      count: result.service_routes_synced,
      className: "text-green-600 dark:text-green-400",
    },
    {
      label: "Routes Deleted",
      count: result.service_routes_deleted,
      className: "text-yellow-600 dark:text-yellow-400",
    },
  ];
  return (
    <Card className="animate-in fade-in-0 slide-in-from-top-2 border-blue-200 p-4 dark:border-blue-800">
      <h3 className="mb-3 text-sm font-semibold">Reconciliation Results</h3>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {stats.map((stat) => (
          <div key={stat.label} className="rounded-md bg-muted/40 p-3 text-center">
            <p className={cn("text-2xl font-bold tabular-nums", stat.className)}>
              {stat.count}
            </p>
            <p className="text-xs text-muted-foreground">{stat.label}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}

function GatewayHistoryNotice() {
  return (
    <Card className="p-4">
      <div className="flex items-start gap-3">
        <Clock className="mt-0.5 size-4 text-muted-foreground" />
        <div className="space-y-1">
          <h2 className="text-sm font-semibold">Gateway Deployment History</h2>
          <p className="text-sm text-muted-foreground">
            Unavailable. The system does not currently persist gateway
            sync/rollback/delete events, so this page no longer fabricates a
            deployment timeline from artifact creation timestamps.
          </p>
        </div>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function GatewayPage() {
  const { data: servicesData, isLoading, error, refetch } = useServices();
  const services = React.useMemo(() => servicesData?.services ?? [], [servicesData]);
  const [artifactVersionsByService, setArtifactVersionsByService] =
    React.useState<Record<string, ArtifactVersionResponse[]>>({});
  const [artifactVersionErrorsByService, setArtifactVersionErrorsByService] =
    React.useState<Record<string, string>>({});
  const [gatewayRoutesById, setGatewayRoutesById] = React.useState<
    Record<string, GatewayRouteDocument>
  >({});
  const [gatewayRoutesLoadFailed, setGatewayRoutesLoadFailed] = React.useState(false);

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

  const loadArtifactVersions = React.useCallback(
    async (serviceList: ServiceSummary[]) => {
      if (serviceList.length === 0) {
        setArtifactVersionsByService({});
        setArtifactVersionErrorsByService({});
        return;
      }

      const results = await Promise.all(
        serviceList.map(async (service) => {
          try {
            const response = await artifactApi.listVersions(service.service_id);
            return {
              serviceId: service.service_id,
              versions: response.versions,
              error: undefined,
            } as const;
          } catch (error) {
            return {
              serviceId: service.service_id,
              versions: undefined,
              error:
                error instanceof Error
                  ? error.message
                  : "Unknown artifact version error",
            } as const;
          }
        }),
      );

      setArtifactVersionsByService(
        Object.fromEntries(
          results
            .filter((result) => result.error == null)
            .map((result) => [result.serviceId, result.versions ?? []]),
        ),
      );
      setArtifactVersionErrorsByService(
        Object.fromEntries(
          results
            .filter((result) => result.error != null)
            .map((result) => [
              result.serviceId,
              `Failed to load artifact versions: ${result.error}`,
            ]),
        ),
      );
    },
    [],
  );

  const loadGatewayRoutes = React.useCallback(async () => {
    try {
      const response = await gatewayApi.listRoutes();
      setGatewayRoutesById(
        Object.fromEntries(response.routes.map((route) => [route.route_id, route])),
      );
      setGatewayRoutesLoadFailed(false);
    } catch {
      setGatewayRoutesById({});
      setGatewayRoutesLoadFailed(true);
    }
  }, []);

  const refreshGatewayState = React.useCallback(async () => {
    await Promise.all([
      refetch(),
      loadArtifactVersions(services),
      loadGatewayRoutes(),
    ]);
  }, [loadArtifactVersions, loadGatewayRoutes, refetch, services]);

  React.useEffect(() => {
    let cancelled = false;

    async function loadGatewayPageState() {
      await Promise.all([
        loadArtifactVersions(services),
        loadGatewayRoutes(),
      ]);

      if (cancelled) {
        return;
      }
    }

    void loadGatewayPageState();

    return () => {
      cancelled = true;
    };
  }, [loadArtifactVersions, loadGatewayRoutes, services]);

  const serviceRoutes: ServiceRoute[] = React.useMemo(
    () =>
      services.map((service) => {
        const versionLoadError = artifactVersionErrorsByService[service.service_id];
        const versions = artifactVersionsByService[service.service_id] ?? [];
        const activeVersion = findActiveArtifactVersion(versions);

        return {
          service,
          status: gatewayRoutesLoadFailed || versionLoadError
            ? "error"
            : inferRouteStatus(activeVersion?.route_config, gatewayRoutesById),
          artifactTimestamp: activeVersion?.created_at ?? service.last_compiled,
          routeConfig: activeVersion?.route_config,
          versionLoadError,
        };
      }),
    [
      artifactVersionErrorsByService,
      artifactVersionsByService,
      gatewayRoutesById,
      gatewayRoutesLoadFailed,
      services,
    ],
  );

  React.useEffect(() => {
    if (!rollbackDialogOpen || !selectedServiceId || selectedVersion) {
      return;
    }

    const previousVersion = previousVersionForService(
      artifactVersionsByService[selectedServiceId] ?? [],
    );
    if (previousVersion) {
      setSelectedVersion(String(previousVersion.version_number));
    }
  }, [
    artifactVersionsByService,
    rollbackDialogOpen,
    selectedServiceId,
    selectedVersion,
  ]);

  const counts = React.useMemo(() => {
    const c = { synced: 0, drifted: 0, error: 0 };
    for (const route of serviceRoutes) c[route.status]++;
    return c;
  }, [serviceRoutes]);
  const servicesErrorMessage =
    error instanceof Error
      ? error.message
      : "The services request did not succeed.";

  async function getArtifactVersionsForService(serviceId: string) {
    const cached = artifactVersionsByService[serviceId];
    if (cached) {
      return cached;
    }

    try {
      const response = await artifactApi.listVersions(serviceId);
      setArtifactVersionsByService((prev) => ({
        ...prev,
        [serviceId]: response.versions,
      }));
      setArtifactVersionErrorsByService((prev) => {
        const next = { ...prev };
        delete next[serviceId];
        return next;
      });
      return response.versions;
    } catch (error) {
      setArtifactVersionErrorsByService((prev) => ({
        ...prev,
        [serviceId]: `Failed to load artifact versions: ${
          error instanceof Error ? error.message : "Unknown artifact version error"
        }`,
      }));
      throw error;
    }
  }

  function gatewayPreviousRoutesForService(
    serviceId: string,
  ): GatewayPreviousRoutes {
    return Object.fromEntries(
      Object.entries(gatewayRoutesById).filter(
        ([, route]) => route.service_id === serviceId,
      ),
    );
  }

  function previousVersionForService(
    versions: ArtifactVersionResponse[],
  ): ArtifactVersionResponse | undefined {
    const currentVersion =
      findActiveArtifactVersion(versions) ??
      versions.find((version) => version.is_active);
    if (!currentVersion) {
      return undefined;
    }

    return [...versions]
      .filter((version) => version.version_number < currentVersion.version_number)
      .sort((left, right) => right.version_number - left.version_number)[0];
  }

  function defaultVersionForService(serviceId?: string): string {
    if (!serviceId) {
      return "1";
    }

    const service = services.find((item) => item.service_id === serviceId);
    return String(service?.active_version ?? 1);
  }

  function rollbackVersionForService(serviceId?: string): string {
    if (!serviceId) {
      return "";
    }

    const versions = artifactVersionsByService[serviceId] ?? [];
    const previousVersion = previousVersionForService(versions);
    if (!previousVersion) {
      return "";
    }

    return String(previousVersion.version_number);
  }

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
      await refreshGatewayState();
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
      const versions = await getArtifactVersionsForService(selectedServiceId);
      const versionNumber = Number(selectedVersion) || 1;
      const targetVersion = findArtifactVersion(versions, versionNumber);
      if (!targetVersion?.route_config) {
        throw new Error(
          `No route configuration found for ${selectedServiceId} version ${versionNumber}.`,
        );
      }

      const result = await gatewayApi.syncRoutes({
        route_config: targetVersion.route_config,
        previous_routes: gatewayPreviousRoutesForService(selectedServiceId),
      });
      toast.success(
        `Synced ${result.service_routes_synced} route(s) for ${selectedServiceId}.`,
      );
      setSyncDialogOpen(false);
      await refreshGatewayState();
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
      const versions = await getArtifactVersionsForService(selectedServiceId);
      const currentVersion = findActiveArtifactVersion(versions);
      if (!currentVersion?.route_config) {
        throw new Error(
          `No active route configuration found for ${selectedServiceId}.`,
        );
      }

      const targetVersionNumber =
        Number(selectedVersion) ||
        previousVersionForService(versions)?.version_number ||
        0;
      if (targetVersionNumber < 1) {
        throw new Error("No previous version is available to roll back to.");
      }

      const targetVersion = findArtifactVersion(versions, targetVersionNumber);
      if (!targetVersion?.route_config) {
        throw new Error(
          `No route configuration found for ${selectedServiceId} version ${targetVersionNumber}.`,
        );
      }

      const result = await gatewayApi.rollbackRoutes({
        route_config: currentVersion.route_config,
        previous_routes: buildPreviousRoutes(targetVersion.route_config),
      });
      toast.success(
        `Rollback restored ${result.service_routes_synced} route(s) for ${selectedServiceId}.`,
      );
      setRollbackDialogOpen(false);
      await refreshGatewayState();
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
      const versions = await getArtifactVersionsForService(selectedServiceId);
      const activeVersion = findActiveArtifactVersion(versions);
      if (!activeVersion?.route_config) {
        throw new Error(
          `No active route configuration found for ${selectedServiceId}.`,
        );
      }

      const result = await gatewayApi.deleteRoutes({
        route_config: activeVersion.route_config,
        previous_routes: {},
      });
      toast.success(
        `Deleted ${result.service_routes_deleted} route(s) for ${selectedServiceId}.`,
      );
      setDeleteDialogOpen(false);
      await refreshGatewayState();
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
    setSelectedVersion(defaultVersionForService(serviceId));
    setSyncDialogOpen(true);
  };

  const openRollbackDialog = async (serviceId?: string) => {
    setSelectedServiceId(serviceId ?? "");
    setSelectedVersion(rollbackVersionForService(serviceId));
    setRollbackDialogOpen(true);
    if (!serviceId) {
      return;
    }

    try {
      const versions = await getArtifactVersionsForService(serviceId);
      const previousVersion = previousVersionForService(versions);
      setSelectedVersion(
        previousVersion ? String(previousVersion.version_number) : "",
      );
    } catch (err) {
      toast.error(
        `Failed to load versions: ${err instanceof Error ? err.message : "Unknown error"}`,
      );
    }
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
            onClick={() => {
              void refreshGatewayState();
            }}
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

      {gatewayRoutesLoadFailed && (
        <Card className="border-destructive/50 bg-destructive/5 p-4 text-sm text-destructive">
          Failed to load live gateway routes. Route status is unavailable until the
          gateway route listing succeeds.
        </Card>
      )}

      {Object.keys(artifactVersionErrorsByService).length > 0 && (
        <Card className="border-destructive/50 bg-destructive/5 p-4 text-sm text-destructive">
          Failed to load artifact versions for{" "}
          {Object.keys(artifactVersionErrorsByService)
            .sort()
            .join(", ")}
          . Those services are shown as errors instead of being treated as having
          empty version histories.
        </Card>
      )}

      {/* Overview Cards */}
      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-20 rounded-lg" />
          ))}
        </div>
      ) : error ? (
        <ErrorState
          title="Failed to load services"
          message={servicesErrorMessage}
          onAction={() => {
            void refreshGatewayState();
          }}
        />
      ) : (
        <OverviewCards
          synced={counts.synced}
          drifted={counts.drifted}
          errors={counts.error}
        />
      )}

      {!error && (
        <>
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
                  <TableHead>Artifact Timestamp</TableHead>
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
                          {route.versionLoadError && (
                            <p className="mt-1 text-xs font-normal text-destructive">
                              Artifact versions unavailable
                            </p>
                          )}
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
                          {relativeTime(route.artifactTimestamp)}
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
                            <RouteConfigViewer
                              config={route.routeConfig}
                              errorMessage={route.versionLoadError}
                            />
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
        </>
      )}

      <GatewayHistoryNotice />

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
            <div className="space-y-2">
              <label className="text-sm font-medium">Target Version</label>
              <Input
                type="number"
                placeholder="Previous version number"
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
