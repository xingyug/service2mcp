"use client";

import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  RefreshCw,
  Code,
  Shield,
  Search,
  CheckCircle2,
  XCircle,
  Clock,
  Trash2,
  Diff,
  Radio,
  RotateCcw,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
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
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ProtocolBadge } from "@/components/services/protocol-badge";
import { ToolCard } from "@/components/services/tool-card";
import { IREditor } from "@/components/services/ir-editor";
import { VersionDiffDialog } from "@/components/services/version-diff-dialog";
import { ReviewStatusBadge } from "@/components/review/review-status-badge";
import { useService, useArtifactVersions } from "@/hooks/use-api";
import { artifactApi, gatewayApi } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";
import type { Operation, ServiceIR } from "@/types/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

// ---------------------------------------------------------------------------
// Tab: Tools
// ---------------------------------------------------------------------------

function ToolsTab({ operations }: { operations: Operation[] }) {
  const [search, setSearch] = React.useState("");
  const [riskFilter, setRiskFilter] = React.useState<string>("all");
  const [enabledFilter, setEnabledFilter] = React.useState<string>("all");
  const [intentFilter, setIntentFilter] = React.useState<string>("all");

  const filtered = React.useMemo(() => {
    let result = operations;
    if (riskFilter !== "all") {
      result = result.filter((o) => o.risk.risk_level === riskFilter);
    }
    if (enabledFilter !== "all") {
      const wantEnabled = enabledFilter === "enabled";
      result = result.filter((o) => o.enabled === wantEnabled);
    }
    if (intentFilter !== "all") {
      result = result.filter((o) => o.tool_intent === intentFilter);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter((o) => o.name.toLowerCase().includes(q));
    }
    return result;
  }, [operations, riskFilter, enabledFilter, intentFilter, search]);

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <Select value={riskFilter} onValueChange={(v) => setRiskFilter(v ?? "all")}>
          <SelectTrigger size="sm" className="w-32">
            <SelectValue placeholder="Risk level" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All risks</SelectItem>
            <SelectItem value="safe">Safe</SelectItem>
            <SelectItem value="cautious">Cautious</SelectItem>
            <SelectItem value="dangerous">Dangerous</SelectItem>
            <SelectItem value="unknown">Unknown</SelectItem>
          </SelectContent>
        </Select>

        <Select value={enabledFilter} onValueChange={(v) => setEnabledFilter(v ?? "all")}>
          <SelectTrigger size="sm" className="w-32">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All status</SelectItem>
            <SelectItem value="enabled">Enabled</SelectItem>
            <SelectItem value="disabled">Disabled</SelectItem>
          </SelectContent>
        </Select>

        <Select value={intentFilter} onValueChange={(v) => setIntentFilter(v ?? "all")}>
          <SelectTrigger size="sm" className="w-32">
            <SelectValue placeholder="Intent" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All intents</SelectItem>
            <SelectItem value="discovery">Discovery</SelectItem>
            <SelectItem value="action">Action</SelectItem>
          </SelectContent>
        </Select>

        <div className="relative ml-auto w-56">
          <Search className="absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search tools…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 pl-8"
          />
        </div>
      </div>

      <p className="text-sm text-muted-foreground">
        {filtered.length} tool{filtered.length !== 1 ? "s" : ""}
      </p>

      {/* Tool list */}
      {filtered.length === 0 ? (
        <p className="py-8 text-center text-muted-foreground">
          No tools match the current filters.
        </p>
      ) : (
        <div className="space-y-2">
          {filtered.map((op) => (
            <ToolCard key={op.id} operation={op} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Versions
// ---------------------------------------------------------------------------

function VersionsTab({ serviceId }: { serviceId: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useArtifactVersions(serviceId);
  const versions = data?.versions ?? [];

  const [deleteTarget, setDeleteTarget] = React.useState<number | null>(null);
  const [deleting, setDeleting] = React.useState(false);
  const [activatingVersion, setActivatingVersion] = React.useState<number | null>(
    null,
  );

  async function refreshVersionData() {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: queryKeys.artifacts.versions(serviceId),
      }),
      queryClient.invalidateQueries({
        queryKey: queryKeys.services.detail(serviceId),
      }),
      queryClient.invalidateQueries({
        queryKey: queryKeys.services.all,
      }),
    ]);
  }

  async function handleDelete() {
    if (deleteTarget === null) {
      return;
    }

    setDeleting(true);
    try {
      await artifactApi.deleteVersion(serviceId, deleteTarget);
      toast.success(`Deleted version v${deleteTarget}.`);
      await refreshVersionData();
    } catch (error) {
      toast.error(
        `Delete failed: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  }

  async function handleActivate(versionNumber: number) {
    setActivatingVersion(versionNumber);
    try {
      await artifactApi.activateVersion(serviceId, versionNumber);
      toast.success(`Activated version v${versionNumber}.`);
      await refreshVersionData();
    } catch (error) {
      toast.error(
        `Activation failed: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    } finally {
      setActivatingVersion(null);
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (versions.length === 0) {
    return (
      <p className="py-8 text-center text-muted-foreground">
        No versions found.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end">
        <VersionDiffDialog serviceId={serviceId} />
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Version</TableHead>
            <TableHead>Created</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Validated</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {versions.map((v) => (
            <TableRow key={v.version_number}>
              <TableCell className="font-medium">
                v{v.version_number}
              </TableCell>
              <TableCell>
                <span className="flex items-center gap-1 text-muted-foreground">
                  <Clock className="size-3.5" />
                  {formatDate(v.created_at)}
                </span>
              </TableCell>
              <TableCell>
                {v.is_active ? (
                  <Badge variant="default" className="bg-green-600">
                    Active
                  </Badge>
                ) : (
                  <Badge variant="outline">Inactive</Badge>
                )}
              </TableCell>
              <TableCell>
                {v.ir ? (
                  <CheckCircle2 className="size-4 text-green-500" />
                ) : (
                  <XCircle className="size-4 text-muted-foreground" />
                )}
              </TableCell>
              <TableCell className="text-right">
                <div className="flex items-center justify-end gap-1">
                  {!v.is_active && (
                    <Button
                      variant="ghost"
                      size="xs"
                      disabled={activatingVersion === v.version_number}
                      onClick={() => handleActivate(v.version_number)}
                    >
                      <Radio className="mr-1 size-3" />
                      {activatingVersion === v.version_number
                        ? "Activating…"
                        : "Activate"}
                    </Button>
                  )}
                  <VersionDiffDialog
                    serviceId={serviceId}
                    initialFrom={v.version_number}
                    trigger={
                      <Button variant="ghost" size="xs">
                        <Diff className="mr-1 size-3" />
                        Diff
                      </Button>
                    }
                  />
                  {!v.is_active && (
                    <Button
                      variant="ghost"
                      size="xs"
                      className="text-destructive hover:text-destructive"
                      onClick={() => setDeleteTarget(v.version_number)}
                    >
                      <Trash2 className="mr-1 size-3" />
                      Delete
                    </Button>
                  )}
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {/* Delete confirmation dialog */}
      <Dialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Version</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete version{" "}
              <strong>v{deleteTarget}</strong>? This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleting}
              onClick={() => deleteTarget && handleDelete()}
            >
              {deleting ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: IR
// ---------------------------------------------------------------------------

function IRTab({ serviceId }: { serviceId: string }) {
  const { data, isLoading } = useArtifactVersions(serviceId);
  const activeVersion = data?.versions?.find((v) => v.is_active);
  const ir: ServiceIR | undefined = activeVersion?.ir;

  if (isLoading) {
    return <Skeleton className="h-96 w-full" />;
  }

  if (!ir) {
    return (
      <p className="py-8 text-center text-muted-foreground">
        No active IR available.
      </p>
    );
  }

  return <IREditor ir={ir} readOnly />;
}

// ---------------------------------------------------------------------------
// Tab: Gateway
// ---------------------------------------------------------------------------

function GatewayTab({
  serviceId,
  onShowVersions,
}: {
  serviceId: string;
  onShowVersions: () => void;
}) {
  const { data, isLoading } = useArtifactVersions(serviceId);
  const activeVersion = data?.versions?.find((v) => v.is_active);
  const routeConfig = activeVersion?.route_config;
  const [syncing, setSyncing] = React.useState(false);
  const [reconciling, setReconciling] = React.useState(false);

  async function handleSync() {
    if (!routeConfig) {
      toast.error("No active route configuration is available for this service.");
      onShowVersions();
      return;
    }

    setSyncing(true);
    try {
      const result = await gatewayApi.syncRoutes({
        route_config: routeConfig,
        previous_routes: {},
      });
      toast.success(`Synced ${result.service_routes_synced} gateway route(s).`);
    } catch (error) {
      toast.error(
        `Gateway sync failed: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    } finally {
      setSyncing(false);
    }
  }

  async function handleReconcile() {
    setReconciling(true);
    try {
      const result = await gatewayApi.reconcile();
      const totalChanged =
        result.consumers_synced +
        result.consumers_deleted +
        result.policy_bindings_synced +
        result.policy_bindings_deleted +
        result.service_routes_synced +
        result.service_routes_deleted;
      toast.success(`Reconciled ${totalChanged} gateway resource(s).`);
    } catch (error) {
      toast.error(
        `Gateway reconcile failed: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    } finally {
      setReconciling(false);
    }
  }

  if (isLoading) {
    return <Skeleton className="h-48 w-full" />;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <h3 className="text-sm font-medium">Route Configuration</h3>
          <p className="text-xs text-muted-foreground">
            Gateway route status for this service
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={handleSync} disabled={syncing}>
            <RefreshCw className="mr-1 size-4" />
            {syncing ? "Syncing…" : "Sync"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleReconcile}
            disabled={reconciling}
          >
            <RotateCcw className="mr-1 size-4" />
            {reconciling ? "Reconciling…" : "Reconcile"}
          </Button>
        </div>
      </div>

      <Separator />

      {routeConfig ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Status:</span>
            <Badge variant="default" className="bg-green-600">
              Active
            </Badge>
          </div>
          <ScrollArea className="h-64 rounded-lg border">
            <pre className="p-4 text-xs leading-relaxed">
              <code>{JSON.stringify(routeConfig, null, 2)}</code>
            </pre>
          </ScrollArea>
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
          <p className="text-muted-foreground">
            No route configuration found for this service.
          </p>
          <Button variant="outline" size="sm" onClick={onShowVersions}>
            <RefreshCw className="mr-1 size-4" />
            Sync Routes
          </Button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ServiceDetailPage() {
  const params = useParams<{ serviceId: string }>();
  const router = useRouter();
  const serviceId = params.serviceId;
  const [activeTab, setActiveTab] = React.useState("tools");

  const { data: service, isLoading, error } = useService(serviceId);
  const { data: versionsData } = useArtifactVersions(serviceId);

  const activeVersion = versionsData?.versions?.find((v) => v.is_active);
  const operations: Operation[] = activeVersion?.ir?.operations ?? [];

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (error || !service) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="mr-1 size-4" />
          Back
        </Button>
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error
            ? `Failed to load service: ${(error as Error).message}`
            : "Service not found."}
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
            <h1 className="text-2xl font-bold">{service.name}</h1>
            <ProtocolBadge protocol={service.protocol} />
          </div>
          {service.active_version != null && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Badge variant="secondary">v{service.active_version}</Badge>
              <span>active</span>
              <Separator orientation="vertical" className="h-4" />
              <span>
                {service.version_count} version
                {service.version_count !== 1 ? "s" : ""}
              </span>
              <Separator orientation="vertical" className="h-4" />
              <ReviewStatusBadge
                serviceId={serviceId}
                versionNumber={service.active_version!}
              />
            </div>
          )}
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              router.push(
                `/compilations/new?service_name=${encodeURIComponent(service.name)}`,
              )
            }
          >
            <RefreshCw className="mr-1 size-4" />
            Recompile
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setActiveTab("ir")}
          >
            <Code className="mr-1 size-4" />
            View IR
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              router.push(
                `/policies?resource_id=${encodeURIComponent(serviceId)}`,
              )
            }
          >
            <Shield className="mr-1 size-4" />
            Manage Access
          </Button>
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="tools">
            Tools ({operations.length})
          </TabsTrigger>
          <TabsTrigger value="versions">Versions</TabsTrigger>
          <TabsTrigger value="ir">IR</TabsTrigger>
          <TabsTrigger value="gateway">Gateway</TabsTrigger>
        </TabsList>

        <TabsContent value="tools" className="mt-4">
          <ToolsTab operations={operations} />
        </TabsContent>

        <TabsContent value="versions" className="mt-4">
          <VersionsTab serviceId={serviceId} />
        </TabsContent>

        <TabsContent value="ir" className="mt-4">
          <IRTab serviceId={serviceId} />
        </TabsContent>

        <TabsContent value="gateway" className="mt-4">
          <GatewayTab
            serviceId={serviceId}
            onShowVersions={() => setActiveTab("versions")}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
