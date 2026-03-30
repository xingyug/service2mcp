"use client";

import * as React from "react";
import { ArrowRightLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useArtifactVersions } from "@/hooks/use-api";
import { VersionDiff } from "@/components/services/version-diff";
import type { ServiceScope } from "@/types/api";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface VersionDiffDialogProps {
  serviceId: string;
  scope?: ServiceScope;
  /** Pre-selected "from" version (optional). */
  initialFrom?: number;
  /** Pre-selected "to" version (optional). */
  initialTo?: number;
  /** Custom trigger element. If omitted a default button is rendered. */
  trigger?: React.ReactElement;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function VersionDiffDialog({
  serviceId,
  scope,
  initialFrom,
  initialTo,
  trigger,
}: VersionDiffDialogProps) {
  const { data: versionsData } = useArtifactVersions(serviceId, scope);
  const versions = React.useMemo(() => versionsData?.versions ?? [], [versionsData]);

  const [from, setFrom] = React.useState<number>(initialFrom ?? 0);
  const [to, setTo] = React.useState<number>(initialTo ?? 0);
  const [open, setOpen] = React.useState(false);

  // Default to first two versions when data arrives
  React.useEffect(() => {
    if (versions.length >= 2 && from === 0 && to === 0) {
      const sorted = [...versions].sort(
        (a, b) => a.version_number - b.version_number,
      );
      setFrom(sorted[sorted.length - 2].version_number);
      setTo(sorted[sorted.length - 1].version_number);
    }
  }, [versions, from, to]);

  // Sync props
  React.useEffect(() => {
    if (initialFrom) setFrom(initialFrom);
  }, [initialFrom]);
  React.useEffect(() => {
    if (initialTo) setTo(initialTo);
  }, [initialTo]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          trigger ?? (
            <Button variant="outline" size="sm">
              <ArrowRightLeft className="mr-1 size-4" />
              Compare Versions
            </Button>
          )
        }
      />

      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Compare Versions</DialogTitle>
          <DialogDescription>
            Select two versions to compare their operations and configuration
            changes.
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="max-h-[70vh]">
          <div className="pr-4">
            {from > 0 && to > 0 && from !== to ? (
              <VersionDiff
                serviceId={serviceId}
                scope={scope}
                fromVersion={from}
                toVersion={to}
              />
            ) : (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-muted-foreground">From:</span>
                    <Select
                      value={from > 0 ? String(from) : ""}
                      onValueChange={(v) => setFrom(Number(v))}
                    >
                      <SelectTrigger size="sm" className="w-24">
                        <SelectValue placeholder="Select" />
                      </SelectTrigger>
                      <SelectContent>
                        {versions.map((v) => (
                          <SelectItem
                            key={v.version_number}
                            value={String(v.version_number)}
                          >
                            v{v.version_number}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <ArrowRightLeft className="size-4 text-muted-foreground" />
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-muted-foreground">To:</span>
                    <Select
                      value={to > 0 ? String(to) : ""}
                      onValueChange={(v) => setTo(Number(v))}
                    >
                      <SelectTrigger size="sm" className="w-24">
                        <SelectValue placeholder="Select" />
                      </SelectTrigger>
                      <SelectContent>
                        {versions.map((v) => (
                          <SelectItem
                            key={v.version_number}
                            value={String(v.version_number)}
                          >
                            v{v.version_number}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <p className="py-8 text-center text-muted-foreground">
                  Select two different versions to compare.
                </p>
              </div>
            )}
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}
