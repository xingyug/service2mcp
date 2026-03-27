"use client";

import * as React from "react";
import {
  ChevronDown,
  ChevronRight,
  Search,
  ChevronsDownUp,
  ChevronsUpDown,
  FolderOpen,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from "@/components/ui/collapsible";
import { ToolCard } from "@/components/services/tool-card";
import type { ToolGroup, Operation } from "@/types/api";

interface ToolGroupViewProps {
  groups: ToolGroup[];
  operations: Operation[];
}

export function ToolGroupView({ groups, operations }: ToolGroupViewProps) {
  const [search, setSearch] = React.useState("");
  const [openGroups, setOpenGroups] = React.useState<Set<string>>(() =>
    new Set(groups.map((g) => g.group_id)),
  );

  const operationMap = React.useMemo(() => {
    const map = new Map<string, Operation>();
    for (const op of operations) map.set(op.id, op);
    return map;
  }, [operations]);

  const groupedOperationIds = React.useMemo(() => {
    const ids = new Set<string>();
    for (const g of groups) {
      for (const id of g.operation_ids) ids.add(id);
    }
    return ids;
  }, [groups]);

  const ungroupedOperations = React.useMemo(
    () => operations.filter((op) => !groupedOperationIds.has(op.id)),
    [operations, groupedOperationIds],
  );

  const lowerSearch = search.toLowerCase();

  const matchesSearch = React.useCallback(
    (op: Operation) =>
      !search ||
      op.name.toLowerCase().includes(lowerSearch) ||
      op.description.toLowerCase().includes(lowerSearch),
    [search, lowerSearch],
  );

  const filteredGroups = React.useMemo(
    () =>
      groups
        .map((g) => ({
          ...g,
          filteredOps: g.operation_ids
            .map((id) => operationMap.get(id))
            .filter((op): op is Operation => !!op && matchesSearch(op)),
        }))
        .filter(
          (g) =>
            g.filteredOps.length > 0 ||
            (!search &&
              (g.label.toLowerCase().includes(lowerSearch) ||
                g.description.toLowerCase().includes(lowerSearch))),
        ),
    [groups, operationMap, matchesSearch, search, lowerSearch],
  );

  const filteredUngrouped = React.useMemo(
    () => ungroupedOperations.filter(matchesSearch),
    [ungroupedOperations, matchesSearch],
  );

  const allExpanded =
    openGroups.size >=
    groups.length + (ungroupedOperations.length > 0 ? 1 : 0);

  const toggleAll = () => {
    if (allExpanded) {
      setOpenGroups(new Set());
    } else {
      const ids = groups.map((g) => g.group_id);
      if (ungroupedOperations.length > 0) ids.push("__ungrouped__");
      setOpenGroups(new Set(ids));
    }
  };

  const toggleGroup = (id: string) => {
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="space-y-4">
      {/* Search + expand toggle */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search across all groups…"
            value={search}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
              setSearch(e.target.value)
            }
            className="pl-9"
          />
        </div>
        <Button variant="outline" size="sm" onClick={toggleAll}>
          {allExpanded ? (
            <>
              <ChevronsDownUp className="size-4" />
              Collapse All
            </>
          ) : (
            <>
              <ChevronsUpDown className="size-4" />
              Expand All
            </>
          )}
        </Button>
      </div>

      {/* Groups */}
      {filteredGroups.map((group) => {
        const isOpen = openGroups.has(group.group_id);
        return (
          <Collapsible
            key={group.group_id}
            open={isOpen}
            onOpenChange={() => toggleGroup(group.group_id)}
          >
            <CollapsibleTrigger
              render={
                <button
                  type="button"
                  className="flex w-full items-center gap-3 rounded-lg border bg-card px-4 py-3 text-left transition-colors hover:bg-muted/30"
                />
              }
            >
              {isOpen ? (
                <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{group.label}</span>
                  <Badge variant="secondary" className="tabular-nums">
                    {group.filteredOps.length}
                  </Badge>
                </div>
                {group.description && (
                  <p className="mt-0.5 line-clamp-1 text-sm text-muted-foreground">
                    {group.description}
                  </p>
                )}
              </div>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="mt-2 space-y-2 pl-4">
                {group.filteredOps.map((op) => (
                  <ToolCard key={op.id} operation={op} />
                ))}
              </div>
            </CollapsibleContent>
          </Collapsible>
        );
      })}

      {/* Ungrouped */}
      {filteredUngrouped.length > 0 && (
        <Collapsible
          open={openGroups.has("__ungrouped__")}
          onOpenChange={() => toggleGroup("__ungrouped__")}
        >
          <CollapsibleTrigger
            render={
              <button
                type="button"
                className="flex w-full items-center gap-3 rounded-lg border border-dashed bg-card px-4 py-3 text-left transition-colors hover:bg-muted/30"
              />
            }
          >
            {openGroups.has("__ungrouped__") ? (
              <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
            ) : (
              <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
            )}
            <FolderOpen className="size-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-muted-foreground">
                  Ungrouped
                </span>
                <Badge variant="secondary" className="tabular-nums">
                  {filteredUngrouped.length}
                </Badge>
              </div>
            </div>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="mt-2 space-y-2 pl-4">
              {filteredUngrouped.map((op) => (
                <ToolCard key={op.id} operation={op} />
              ))}
            </div>
          </CollapsibleContent>
        </Collapsible>
      )}

      {/* Empty state */}
      {filteredGroups.length === 0 && filteredUngrouped.length === 0 && (
        <div className="rounded-lg border border-dashed py-8 text-center">
          <Search className="mx-auto mb-2 size-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            {search
              ? "No operations match your search."
              : "No tool groups available."}
          </p>
        </div>
      )}
    </div>
  );
}
