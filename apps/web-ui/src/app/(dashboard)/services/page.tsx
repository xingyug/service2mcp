"use client";

import * as React from "react";
import Link from "next/link";
import {
  Server,
  Search,
  LayoutGrid,
  List,
  Plus,
  Clock,
  Layers,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ServiceCard } from "@/components/services/service-card";
import { ProtocolBadge } from "@/components/services/protocol-badge";
import { useServices } from "@/hooks/use-api";
import {
  buildScopedServiceKey,
  buildServiceDetailHref,
} from "@/lib/service-scope";

const PROTOCOLS = [
  "all",
  "openapi",
  "rest",
  "graphql",
  "grpc",
  "jsonrpc",
  "odata",
  "scim",
  "soap",
  "sql",
] as const;

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

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ServicesPage() {
  const [search, setSearch] = React.useState("");
  const [protocol, setProtocol] = React.useState<string>("all");
  const [tenant, setTenant] = React.useState<string>("all");
  const [environment, setEnvironment] = React.useState<string>("all");
  const [view, setView] = React.useState<"grid" | "list">("grid");

  const { data, isLoading, error } = useServices();
  const services = React.useMemo(() => data?.services ?? [], [data]);

  // Derive unique tenants / environments for filter dropdowns
  const tenants = React.useMemo(
    () => [...new Set(services.map((s) => s.tenant).filter(Boolean))] as string[],
    [services],
  );
  const environments = React.useMemo(
    () =>
      [...new Set(services.map((s) => s.environment).filter(Boolean))] as string[],
    [services],
  );

  // Apply filters
  const filtered = React.useMemo(() => {
    let result = services;
    if (protocol !== "all") {
      result = result.filter(
        (s) => s.protocol.toLowerCase() === protocol,
      );
    }
    if (tenant !== "all") {
      result = result.filter((s) => s.tenant === tenant);
    }
    if (environment !== "all") {
      result = result.filter((s) => s.environment === environment);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.service_id.toLowerCase().includes(q),
      );
    }
    return result;
  }, [services, protocol, tenant, environment, search]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold">Service Registry</h1>
            {!isLoading && (
              <Badge variant="secondary">{services.length}</Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground">
            Browse and manage compiled MCP tool servers
          </p>
        </div>
        <Link href="/compilations/new">
          <Button size="sm">
            <Plus className="mr-1 size-4" />
            New Compilation
          </Button>
        </Link>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Protocol chips */}
        <div className="flex flex-wrap gap-1">
          {PROTOCOLS.map((p) => (
            <Button
              key={p}
              variant={protocol === p ? "default" : "outline"}
              size="xs"
              onClick={() => setProtocol(p)}
              className="capitalize"
            >
              {p === "all" ? "All" : p}
            </Button>
          ))}
        </div>

        {/* Tenant dropdown */}
        {tenants.length > 0 && (
          <Select value={tenant} onValueChange={(v) => setTenant(v ?? "all")}>
            <SelectTrigger size="sm" className="w-36">
              <SelectValue placeholder="Tenant" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All tenants</SelectItem>
              {tenants.map((t) => (
                <SelectItem key={t} value={t}>
                  {t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        {/* Environment dropdown */}
        {environments.length > 0 && (
          <Select value={environment} onValueChange={(v) => setEnvironment(v ?? "all")}>
            <SelectTrigger size="sm" className="w-40">
              <SelectValue placeholder="Environment" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All environments</SelectItem>
              {environments.map((e) => (
                <SelectItem key={e} value={e}>
                  {e}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        {/* Search */}
        <div className="relative ml-auto w-64">
          <Search className="absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search services…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 pl-8"
          />
        </div>

        {/* View toggle */}
        <div className="flex rounded-lg border">
          <Button
            variant={view === "grid" ? "secondary" : "ghost"}
            size="icon-xs"
            onClick={() => setView("grid")}
            aria-label="Grid view"
          >
            <LayoutGrid className="size-4" />
          </Button>
          <Button
            variant={view === "list" ? "secondary" : "ghost"}
            size="icon-xs"
            onClick={() => setView("list")}
            aria-label="List view"
          >
            <List className="size-4" />
          </Button>
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-36 rounded-xl" />
          ))}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load services: {(error as Error).message}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !error && filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
          <Server className="size-12 text-muted-foreground/40" />
          <div>
            <p className="text-lg font-medium">No services compiled yet</p>
            <p className="text-sm text-muted-foreground">
              Run a compilation to register your first MCP tool server.
            </p>
          </div>
          <Link href="/compilations/new">
            <Button size="sm">
              <Plus className="mr-1 size-4" />
              New Compilation
            </Button>
          </Link>
        </div>
      )}

      {/* Grid view */}
      {!isLoading && !error && filtered.length > 0 && view === "grid" && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((s) => (
            <ServiceCard key={buildScopedServiceKey(s)} service={s} />
          ))}
        </div>
      )}

      {/* List view */}
      {!isLoading && !error && filtered.length > 0 && view === "list" && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Protocol</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Active Version</TableHead>
              <TableHead>Versions</TableHead>
              <TableHead>Last Compiled</TableHead>
              <TableHead>Tenant</TableHead>
              <TableHead>Environment</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((s) => (
              <TableRow key={buildScopedServiceKey(s)} className="cursor-pointer">
                <TableCell>
                  <Link href={buildServiceDetailHref(s)}>
                    <ProtocolBadge protocol={s.protocol} size="sm" />
                  </Link>
                </TableCell>
                <TableCell>
                  <Link
                    href={buildServiceDetailHref(s)}
                    className="font-medium hover:underline"
                  >
                    {s.name}
                  </Link>
                </TableCell>
                <TableCell>
                  {s.active_version != null ? (
                    <Badge variant="secondary">v{s.active_version}</Badge>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell>
                  <span className="flex items-center gap-1">
                    <Layers className="size-3.5" />
                    {s.version_count}
                  </span>
                </TableCell>
                <TableCell>
                  <span className="flex items-center gap-1 text-muted-foreground">
                    <Clock className="size-3.5" />
                    {relativeTime(s.last_compiled)}
                  </span>
                </TableCell>
                <TableCell>{s.tenant ?? "—"}</TableCell>
                <TableCell>{s.environment ?? "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
