"use client";

import * as React from "react";
import { AlertTriangle } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogMedia,
} from "@/components/ui/alert-dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import type { Operation } from "@/types/api";

interface RiskConfirmationDialogProps {
  operation: Operation | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}

function MetadataRow({
  label,
  value,
  danger,
}: {
  label: string;
  value?: boolean;
  danger?: boolean;
}) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <Badge
        variant="secondary"
        className={cn(
          value && danger && "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
          value && !danger && "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
          !value && "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
        )}
      >
        {value ? "Yes" : "No"}
      </Badge>
    </div>
  );
}

export function RiskConfirmationDialog({
  operation,
  open,
  onOpenChange,
  onConfirm,
}: RiskConfirmationDialogProps) {
  const [confirmed, setConfirmed] = React.useState(false);

  // Reset checkbox when dialog opens/closes
  React.useEffect(() => {
    if (open) setConfirmed(false);
  }, [open]);

  if (!operation) return null;

  const risk = operation.risk;

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent size="default" className="sm:max-w-md">
        <AlertDialogHeader>
          <AlertDialogMedia className="bg-red-100 dark:bg-red-900/40">
            <AlertTriangle className="size-6 text-red-600 dark:text-red-400" />
          </AlertDialogMedia>
          <AlertDialogTitle>Dangerous Operation</AlertDialogTitle>
          <AlertDialogDescription>
            You are about to invoke{" "}
            <strong className="text-foreground">{operation.name}</strong>, which
            is classified as{" "}
            <span className="font-semibold text-red-600 dark:text-red-400">
              dangerous
            </span>
            . Please review the risk details below.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-2 rounded-lg border border-red-200 bg-red-50/50 p-3 dark:border-red-800 dark:bg-red-900/10">
          <MetadataRow
            label="Writes State"
            value={risk.writes_state}
            danger
          />
          <MetadataRow
            label="Destructive"
            value={risk.destructive}
            danger
          />
          <MetadataRow
            label="External Side Effect"
            value={risk.external_side_effect}
            danger
          />
          <MetadataRow label="Idempotent" value={risk.idempotent} />
        </div>

        <Separator />

        <label className="flex cursor-pointer items-start gap-3 text-sm">
          <Checkbox
            checked={confirmed}
            onCheckedChange={(checked: boolean) => setConfirmed(checked)}
            className="mt-0.5"
          />
          <span>
            I understand the risks associated with this operation and wish to
            proceed.
          </span>
        </label>

        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={!confirmed}
            onClick={onConfirm}
            className="bg-red-600 text-white hover:bg-red-700 dark:bg-red-600 dark:hover:bg-red-700"
          >
            Proceed
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
