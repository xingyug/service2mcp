"use client";

import * as React from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Progress } from "@/components/ui/progress";
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from "@/components/ui/collapsible";
import { RiskBadge } from "@/components/services/risk-badge";
import type { Operation, ToolIntent } from "@/types/api";

function IntentBadge({ intent }: { intent?: ToolIntent }) {
  if (!intent) return null;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide",
        intent === "discovery"
          ? "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300"
          : "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
      )}
    >
      {intent}
    </span>
  );
}

function DescriptionPrefix({ description }: { description: string }) {
  const match = description.match(/^\[(DISCOVERY|ACTION)]/);
  if (!match) return <>{description}</>;
  const tag = match[1];
  const rest = description.slice(match[0].length).trim();
  return (
    <>
      <Badge
        variant="secondary"
        className={cn(
          "mr-1 text-[10px]",
          tag === "DISCOVERY"
            ? "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300"
            : "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
        )}
      >
        {tag}
      </Badge>
      {rest}
    </>
  );
}

interface ToolCardProps {
  operation: Operation;
  onToggle?: (operationId: string, enabled: boolean) => void;
}

export function ToolCard({ operation, onToggle }: ToolCardProps) {
  const [open, setOpen] = React.useState(false);
  const confidencePct = Math.round(operation.confidence * 100);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className="rounded-lg border bg-card transition-colors hover:bg-muted/30">
        <CollapsibleTrigger
          render={
            <button
              type="button"
              className="flex w-full items-center gap-3 px-4 py-3 text-left"
            />
          }
        >
            {open ? (
              <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
            ) : (
              <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
            )}
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{operation.name}</span>
                <RiskBadge level={operation.risk.risk_level} />
                <IntentBadge intent={operation.tool_intent} />
                {operation.method && operation.path && (
                  <span className="font-mono text-xs text-muted-foreground">
                    {operation.method.toUpperCase()} {operation.path}
                  </span>
                )}
              </div>
              <p className="line-clamp-1 text-sm text-muted-foreground">
                <DescriptionPrefix description={operation.description} />
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <span className="text-xs text-muted-foreground">
                {operation.params.length} param
                {operation.params.length !== 1 ? "s" : ""}
              </span>
              <div className="flex w-16 items-center gap-1">
                <Progress value={confidencePct} className="h-1.5 w-10" />
                <span className="text-[10px] tabular-nums text-muted-foreground">
                  {confidencePct}%
                </span>
              </div>
              <div
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => e.stopPropagation()}
              >
                <Switch
                  checked={operation.enabled}
                  onCheckedChange={(checked: boolean) =>
                    onToggle?.(operation.id, checked)
                  }
                  size="sm"
                />
              </div>
            </div>
        </CollapsibleTrigger>

        <CollapsibleContent>
          <div className="border-t px-4 py-3 text-sm">
            <p className="mb-3 text-muted-foreground">
              <DescriptionPrefix description={operation.description} />
            </p>

            {operation.params.length > 0 && (
              <div className="mb-3">
                <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Parameters
                </h4>
                <div className="space-y-1">
                  {operation.params.map((p) => (
                    <div
                      key={p.name}
                      className="flex items-baseline gap-2 text-xs"
                    >
                      <code className="font-mono font-semibold">{p.name}</code>
                      <span className="text-muted-foreground">{p.type}</span>
                      {p.required && (
                        <Badge variant="outline" className="h-4 text-[10px]">
                          required
                        </Badge>
                      )}
                      <span className="flex-1 truncate text-muted-foreground">
                        {p.description}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {operation.response_schema && (
              <div className="mb-3">
                <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Response Schema
                </h4>
                <pre className="max-h-40 overflow-auto rounded-md bg-muted p-2 text-xs">
                  {JSON.stringify(operation.response_schema, null, 2)}
                </pre>
              </div>
            )}

            <div>
              <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Risk Metadata
              </h4>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span>
                  Writes state:{" "}
                  {operation.risk.writes_state ? "Yes" : "No"}
                </span>
                <span>
                  Destructive:{" "}
                  {operation.risk.destructive ? "Yes" : "No"}
                </span>
                <span>
                  Side effects:{" "}
                  {operation.risk.external_side_effect ? "Yes" : "No"}
                </span>
                <span>
                  Idempotent:{" "}
                  {operation.risk.idempotent ? "Yes" : "No"}
                </span>
                <span>
                  Confidence: {Math.round(operation.risk.confidence * 100)}%
                </span>
                <span>Source: {operation.risk.source}</span>
              </div>
            </div>
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}
