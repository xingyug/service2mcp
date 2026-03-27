"use client";

import * as React from "react";
import { useTheme } from "next-themes";
import {
  Code,
  TreePine,
  Download,
  Save,
  Pencil,
  Eye,
  ChevronDown,
  ChevronRight,
  Braces,
  Hash,
  ToggleLeft,
  Type,
  List,
  Globe,
  Lock,
  Layers,
  Calendar,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { RiskBadge } from "@/components/services/risk-badge";
import type { ServiceIR, RiskLevel, FieldSource } from "@/types/api";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface IREditorProps {
  ir: ServiceIR;
  readOnly?: boolean;
  onSave?: (updatedIR: ServiceIR) => void;
}

// ---------------------------------------------------------------------------
// Source badge
// ---------------------------------------------------------------------------

const sourceColors: Record<FieldSource, string> = {
  extractor:
    "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  llm: "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300",
  user_override:
    "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
};

function SourceBadge({ source }: { source: FieldSource }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium",
        sourceColors[source],
      )}
    >
      {source}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Type icon
// ---------------------------------------------------------------------------

function typeIcon(value: unknown): React.ReactNode {
  if (value === null || value === undefined)
    return <span className="text-muted-foreground">null</span>;
  if (typeof value === "string")
    return <Type className="size-3 text-blue-500" />;
  if (typeof value === "number")
    return <Hash className="size-3 text-orange-500" />;
  if (typeof value === "boolean")
    return <ToggleLeft className="size-3 text-green-500" />;
  if (Array.isArray(value))
    return <List className="size-3 text-purple-500" />;
  if (typeof value === "object")
    return <Braces className="size-3 text-yellow-500" />;
  return null;
}

function typeBadgeText(t: string): string {
  return t;
}

// ---------------------------------------------------------------------------
// Tree node
// ---------------------------------------------------------------------------

function TreeNode({
  label,
  value,
  depth = 0,
  defaultOpen = false,
}: {
  label: string;
  value: unknown;
  depth?: number;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = React.useState(defaultOpen || depth < 1);

  // Primitive values
  if (
    value === null ||
    value === undefined ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return (
      <div
        className="flex items-center gap-2 py-0.5"
        style={{ paddingLeft: `${depth * 16}px` }}
      >
        {typeIcon(value)}
        <span className="text-xs font-medium text-muted-foreground">
          {label}:
        </span>
        <span className="text-xs">
          {value === null
            ? "null"
            : typeof value === "string"
              ? `"${value}"`
              : String(value)}
        </span>
      </div>
    );
  }

  // Arrays
  if (Array.isArray(value)) {
    return (
      <div style={{ paddingLeft: `${depth * 16}px` }}>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="flex items-center gap-1 py-0.5 hover:bg-muted/50 rounded"
        >
          {open ? (
            <ChevronDown className="size-3 text-muted-foreground" />
          ) : (
            <ChevronRight className="size-3 text-muted-foreground" />
          )}
          {typeIcon(value)}
          <span className="text-xs font-medium text-muted-foreground">
            {label}
          </span>
          <Badge variant="outline" className="h-4 text-[10px]">
            {value.length}
          </Badge>
        </button>
        {open && (
          <div>
            {value.map((item, idx) => (
              <TreeNode
                key={idx}
                label={`[${idx}]`}
                value={item}
                depth={depth + 1}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  // Objects
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const entries = Object.entries(obj);

    return (
      <div style={{ paddingLeft: `${depth * 16}px` }}>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="flex items-center gap-1 py-0.5 hover:bg-muted/50 rounded"
        >
          {open ? (
            <ChevronDown className="size-3 text-muted-foreground" />
          ) : (
            <ChevronRight className="size-3 text-muted-foreground" />
          )}
          {typeIcon(value)}
          <span className="text-xs font-medium text-muted-foreground">
            {label}
          </span>
          <Badge variant="outline" className="h-4 text-[10px]">
            {entries.length} keys
          </Badge>
        </button>
        {open && (
          <div>
            {entries.map(([k, v]) => (
              <TreeNode key={k} label={k} value={v} depth={depth + 1} />
            ))}
          </div>
        )}
      </div>
    );
  }

  return null;
}

// ---------------------------------------------------------------------------
// Operation card (tree view)
// ---------------------------------------------------------------------------

function OperationNode({
  operation,
  depth,
}: {
  operation: Record<string, unknown>;
  depth: number;
}) {
  const [open, setOpen] = React.useState(false);
  const name = operation.name as string;
  const method = operation.method as string | undefined;
  const path = operation.path as string | undefined;
  const risk = operation.risk as { risk_level?: RiskLevel } | undefined;
  const source = operation.source as FieldSource | undefined;
  const params = operation.params as Array<Record<string, unknown>> | undefined;

  return (
    <div
      className="rounded-md border bg-card my-1"
      style={{ marginLeft: `${depth * 16}px` }}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/30 rounded-md"
      >
        {open ? (
          <ChevronDown className="size-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 text-muted-foreground" />
        )}
        <span className="text-sm font-semibold">{name}</span>
        {method && path && (
          <span className="font-mono text-xs text-muted-foreground">
            {(method as string).toUpperCase()} {path}
          </span>
        )}
        {risk?.risk_level && <RiskBadge level={risk.risk_level} />}
        {source && <SourceBadge source={source} />}
      </button>
      {open && (
        <div className="border-t px-3 py-2 space-y-1">
          {params && params.length > 0 && (
            <div className="mb-2">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Parameters
              </span>
              <div className="mt-1 space-y-0.5">
                {params.map((p) => (
                  <div
                    key={p.name as string}
                    className="flex items-center gap-2 text-xs"
                  >
                    <code className="font-mono font-medium">
                      {p.name as string}
                    </code>
                    <Badge variant="secondary" className="h-4 text-[10px]">
                      {typeBadgeText(p.type as string)}
                    </Badge>
                    {Boolean(p.required) && (
                      <Badge variant="outline" className="h-4 text-[10px]">
                        required
                      </Badge>
                    )}
                    {Boolean(p.source) && (
                      <SourceBadge source={String(p.source) as FieldSource} />
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          {Object.entries(operation)
            .filter(
              ([k]) =>
                !["name", "method", "path", "params", "risk", "source"].includes(k),
            )
            .map(([k, v]) => (
              <TreeNode key={k} label={k} value={v} depth={0} />
            ))}
          {risk && (
            <div className="pt-1">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Risk Metadata
              </span>
              <div className="mt-1">
                {Object.entries(risk).map(([k, v]) =>
                  k === "risk_level" ? null : (
                    <TreeNode key={k} label={k} value={v} depth={0} />
                  ),
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// IR Tree View
// ---------------------------------------------------------------------------

function IRTreeView({ ir }: { ir: ServiceIR }) {
  return (
    <div className="space-y-1 p-2">
      {/* Top-level scalar fields */}
      {(
        [
          "ir_version",
          "compiler_version",
          "protocol",
          "service_name",
          "service_description",
          "base_url",
          "source_url",
          "source_hash",
          "created_at",
          "tenant",
          "environment",
        ] as const
      ).map((key) => {
        const val = (ir as unknown as Record<string, unknown>)[key];
        if (val === undefined) return null;
        return <TreeNode key={key} label={key} value={val} depth={0} />;
      })}

      {/* Auth */}
      <TreeNode label="auth" value={ir.auth} depth={0} defaultOpen />

      {/* Operations (special rendering) */}
      <div className="py-1">
        <div className="flex items-center gap-2 mb-1">
          <Layers className="size-3.5 text-muted-foreground" />
          <span className="text-xs font-medium text-muted-foreground">
            operations
          </span>
          <Badge variant="outline" className="h-4 text-[10px]">
            {ir.operations.length}
          </Badge>
        </div>
        {ir.operations.map((op) => (
          <OperationNode
            key={op.id}
            operation={op as unknown as Record<string, unknown>}
            depth={1}
          />
        ))}
      </div>

      {/* Metadata */}
      <TreeNode label="metadata" value={ir.metadata} depth={0} />

      {/* Tool grouping */}
      {ir.tool_grouping && (
        <TreeNode label="tool_grouping" value={ir.tool_grouping} depth={0} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lazy Monaco wrapper
// ---------------------------------------------------------------------------

const MonacoEditor = React.lazy(() => import("@monaco-editor/react"));

function CodeView({
  value,
  readOnly,
  onChange,
  theme,
}: {
  value: string;
  readOnly: boolean;
  onChange: (val: string) => void;
  theme: string;
}) {
  return (
    <React.Suspense
      fallback={
        <div className="flex h-[500px] items-center justify-center text-muted-foreground">
          Loading editor…
        </div>
      }
    >
      <MonacoEditor
        height="500px"
        language="json"
        theme={theme === "dark" ? "vs-dark" : "light"}
        value={value}
        onChange={(val) => onChange(val ?? "")}
        options={{
          readOnly,
          minimap: { enabled: true },
          lineNumbers: "on",
          fontSize: 13,
          wordWrap: "on",
          scrollBeyondLastLine: false,
          automaticLayout: true,
        }}
      />
    </React.Suspense>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function IREditor({ ir, readOnly = true, onSave }: IREditorProps) {
  const { resolvedTheme } = useTheme();
  const [editing, setEditing] = React.useState(false);
  const [editorValue, setEditorValue] = React.useState(() =>
    JSON.stringify(ir, null, 2),
  );
  const [parseError, setParseError] = React.useState<string | null>(null);

  // Reset editor value when ir changes
  React.useEffect(() => {
    setEditorValue(JSON.stringify(ir, null, 2));
    setParseError(null);
  }, [ir]);

  const isReadOnly = readOnly || !editing;

  function handleSave() {
    try {
      const parsed = JSON.parse(editorValue) as ServiceIR;
      setParseError(null);
      onSave?.(parsed);
      setEditing(false);
    } catch (e) {
      setParseError((e as Error).message);
    }
  }

  function handleDownload() {
    const blob = new Blob([JSON.stringify(ir, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${ir.service_name}-ir.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-4">
      {/* Metadata header */}
      <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
        <div className="flex items-center gap-1">
          <Layers className="size-3.5 text-muted-foreground" />
          <span className="text-muted-foreground">IR:</span>
          <span className="font-medium">{ir.ir_version}</span>
        </div>
        <div className="flex items-center gap-1">
          <Code className="size-3.5 text-muted-foreground" />
          <span className="text-muted-foreground">Compiler:</span>
          <span className="font-medium">{ir.compiler_version}</span>
        </div>
        <div className="flex items-center gap-1">
          <Globe className="size-3.5 text-muted-foreground" />
          <span className="text-muted-foreground">Protocol:</span>
          <span className="font-medium">{ir.protocol}</span>
        </div>
        <div className="flex items-center gap-1">
          <Globe className="size-3.5 text-muted-foreground" />
          <span className="text-muted-foreground">Base URL:</span>
          <code className="font-mono text-xs">{ir.base_url}</code>
        </div>
        <div className="flex items-center gap-1">
          <Lock className="size-3.5 text-muted-foreground" />
          <span className="text-muted-foreground">Auth:</span>
          <Badge variant="outline">{ir.auth.type}</Badge>
        </div>
        <div className="flex items-center gap-1">
          <Layers className="size-3.5 text-muted-foreground" />
          <span className="text-muted-foreground">Operations:</span>
          <span className="font-medium">{ir.operations.length}</span>
        </div>
        <div className="flex items-center gap-1">
          <Calendar className="size-3.5 text-muted-foreground" />
          <span className="text-muted-foreground">Created:</span>
          <span className="font-medium">
            {new Date(ir.created_at).toLocaleString()}
          </span>
        </div>
      </div>

      <Separator />

      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <Tabs defaultValue="code" className="w-full">
          <div className="flex items-center justify-between mb-3">
            <TabsList>
              <TabsTrigger value="code" className="gap-1">
                <Code className="size-3.5" />
                Code
              </TabsTrigger>
              <TabsTrigger value="tree" className="gap-1">
                <TreePine className="size-3.5" />
                Tree
              </TabsTrigger>
            </TabsList>

            <div className="flex items-center gap-2">
              {!readOnly && (
                <Button
                  variant={editing ? "default" : "outline"}
                  size="sm"
                  onClick={() => {
                    if (editing) {
                      // Cancel edit
                      setEditorValue(JSON.stringify(ir, null, 2));
                      setParseError(null);
                    }
                    setEditing(!editing);
                  }}
                >
                  {editing ? (
                    <>
                      <Eye className="mr-1 size-4" />
                      Cancel
                    </>
                  ) : (
                    <>
                      <Pencil className="mr-1 size-4" />
                      Edit
                    </>
                  )}
                </Button>
              )}
              {editing && (
                <Button size="sm" onClick={handleSave}>
                  <Save className="mr-1 size-4" />
                  Save
                </Button>
              )}
              <Button variant="outline" size="sm" onClick={handleDownload}>
                <Download className="mr-1 size-4" />
                Download JSON
              </Button>
            </div>
          </div>

          {parseError && (
            <div className="mb-3 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              JSON parse error: {parseError}
            </div>
          )}

          <TabsContent value="code" className="mt-0">
            <div className="rounded-lg border overflow-hidden">
              <CodeView
                value={editorValue}
                readOnly={isReadOnly}
                onChange={setEditorValue}
                theme={resolvedTheme ?? "light"}
              />
            </div>
          </TabsContent>

          <TabsContent value="tree" className="mt-0">
            <div className="rounded-lg border max-h-[500px] overflow-auto">
              <IRTreeView ir={ir} />
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
