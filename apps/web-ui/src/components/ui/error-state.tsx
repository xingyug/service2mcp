import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";

interface ErrorStateProps {
  title: string;
  message: string;
  actionLabel?: string;
  onAction?: () => void;
}

export function ErrorState({
  title,
  message,
  actionLabel = "Retry",
  onAction,
}: ErrorStateProps) {
  return (
    <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle className="size-4" />
            {title}
          </p>
          <p>{message}</p>
        </div>
        {onAction && (
          <Button
            variant="outline"
            size="sm"
            className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={onAction}
          >
            {actionLabel}
          </Button>
        )}
      </div>
    </div>
  );
}
