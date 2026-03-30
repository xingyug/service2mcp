"use client";

import * as React from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Clock,
  CheckCircle2,
  XCircle,
  Trash2,
  Diff,
  Radio,
  Code,
  ArrowRightLeft,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
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
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useArtifactVersions, useService } from "@/hooks/use-api";
import { artifactApi } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";
import { VersionDiffDialog } from "@/components/services/version-diff-dialog";
import { IREditor } from "@/components/services/ir-editor";
import { appendServiceScope, serviceScopeFromSearchParams } from "@/lib/service-scope";
import type { ServiceIR } from "@/types/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function VersionsPage() {
  const params = useParams<{ serviceId: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const serviceId = params.serviceId;
  const scope = serviceScopeFromSearchParams(searchParams);

  const { data: service } = useService(serviceId, scope);
  const { data, isLoading, error } = useArtifactVersions(serviceId, scope);
  const versions = data?.versions ?? [];

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = React.useState<number | null>(null);
  const [deleting, setDeleting] = React.useState(false);

  // View IR dialog
  const [viewIrVersion, setViewIrVersion] = React.useState<number | null>(null);
  const selectedIr: ServiceIR | undefined = viewIrVersion
    ? versions.find((v) => v.version_number === viewIrVersion)?.ir
    : undefined;

  async function refreshVersionData() {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.artifacts.versions(serviceId, scope),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.services.detail(serviceId, scope),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.services.all,
      }),
    ]);
  }

  async function handleDelete(version: number) {
    setDeleting(true);
    try {
      await artifactApi.deleteVersion(serviceId, version, scope);
      toast.success(`Deleted version v${version}.`);
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

  async function handleActivate(version: number) {
    try {
      await artifactApi.activateVersion(serviceId, version, scope);
      toast.success(`Activated version v${version}.`);
      await refreshVersionData();
    } catch (error) {
      toast.error(
        `Activation failed: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() =>
              router.push(appendServiceScope(`/services/${serviceId}`, scope))
            }
          >
            <ArrowLeft className="size-4" />
          </Button>
          <h1 className="text-xl font-bold">
            {service?.name ?? "Service"} — Versions
          </h1>
        </div>
        <ErrorState
          title="Failed to load artifact versions"
          message={
            error instanceof Error
              ? error.message
              : "The artifact versions request did not succeed."
          }
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() =>
              router.push(appendServiceScope(`/services/${serviceId}`, scope))
            }
          >
            <ArrowLeft className="size-4" />
          </Button>
          <h1 className="text-xl font-bold">
            {service?.name ?? "Service"} — Versions
          </h1>
        </div>
        <VersionDiffDialog
          serviceId={serviceId}
          scope={scope}
          trigger={
            <Button variant="outline" size="sm">
              <ArrowRightLeft className="mr-1 size-4" />
              Compare Versions
            </Button>
          }
        />
      </div>

      {/* Version table */}
      {versions.length === 0 ? (
        <p className="py-8 text-center text-muted-foreground">
          No versions found.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Version #</TableHead>
              <TableHead>Created At</TableHead>
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
                        onClick={() => handleActivate(v.version_number)}
                      >
                        <Radio className="mr-1 size-3" />
                        Activate
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="xs"
                      onClick={() => setViewIrVersion(v.version_number)}
                    >
                      <Code className="mr-1 size-3" />
                      View IR
                    </Button>
                    <VersionDiffDialog
                      serviceId={serviceId}
                      scope={scope}
                      initialFrom={v.version_number}
                      trigger={
                        <Button variant="ghost" size="xs">
                          <Diff className="mr-1 size-3" />
                          Compare
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
      )}

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
              onClick={() => deleteTarget && handleDelete(deleteTarget)}
            >
              {deleting ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* View IR dialog */}
      <Dialog
        open={viewIrVersion !== null}
        onOpenChange={(open) => {
          if (!open) setViewIrVersion(null);
        }}
      >
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <DialogTitle>IR — Version {viewIrVersion}</DialogTitle>
            <DialogDescription>
              Intermediate Representation for version v{viewIrVersion}
            </DialogDescription>
          </DialogHeader>
          <ScrollArea className="max-h-[70vh]">
            <div className="pr-4">
              {selectedIr ? (
                <IREditor ir={selectedIr} readOnly />
              ) : (
                <p className="py-8 text-center text-muted-foreground">
                  No IR data available for this version.
                </p>
              )}
            </div>
          </ScrollArea>
        </DialogContent>
      </Dialog>
    </div>
  );
}
