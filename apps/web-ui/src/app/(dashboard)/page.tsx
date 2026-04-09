"use client";

import Link from "next/link";
import {
  Server,
  ListChecks,
  Wrench,
  Activity,
  ArrowRight,
} from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { StatusBadge } from "@/components/compilations/status-badge";
import { CompilationMetrics } from "@/components/dashboard/compilation-metrics";
import { useServices, useCompilations, useAuditLogs } from "@/hooks/use-api";
import type { CompilationJobResponse, AuditLogEntry } from "@/types/api";

// ── Helpers ────────────────────────────────────────────────────────────────

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function truncateId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

function stageLabel(stage?: string): string {
  if (!stage) return "—";
  return stage.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

const ACTION_COLORS: Record<string, string> = {
  create: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  delete: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  update: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  revoke: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
};

function actionBadgeClass(action: string): string {
  const key = Object.keys(ACTION_COLORS).find((k) =>
    action.toLowerCase().includes(k),
  );
  return key
    ? ACTION_COLORS[key]
    : "bg-muted text-muted-foreground";
}

// ── Stat card ──────────────────────────────────────────────────────────────

function StatCard({
  icon: Icon,
  title,
  value,
  subtitle,
  loading,
  error,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  value: string | number;
  subtitle?: string;
  loading?: boolean;
  error?: boolean;
}) {
  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center gap-4 p-6">
          <Skeleton className="h-10 w-10 rounded-lg" />
          <div className="space-y-1.5">
            <Skeleton className="h-3.5 w-20" />
            <Skeleton className="h-7 w-14" />
            <Skeleton className="h-3 w-24" />
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-6">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
          <Icon className="h-5 w-5 text-primary" />
        </div>
        <div>
          <p className="text-sm text-muted-foreground">{title}</p>
          <p className="text-2xl font-bold">
            {error ? "—" : value}
          </p>
          {subtitle && (
            <p className="text-xs text-muted-foreground">{subtitle}</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Main dashboard ─────────────────────────────────────────────────────────

const REFETCH_INTERVAL = 30_000;

export default function DashboardPage() {
  const {
    data: servicesData,
    isLoading: servicesLoading,
    isError: servicesError,
  } = useServices(undefined, { refetchInterval: REFETCH_INTERVAL });

  const {
    data: compilations,
    isLoading: compilationsLoading,
    isError: compilationsError,
  } = useCompilations({ refetchInterval: REFETCH_INTERVAL });

  const {
    data: auditData,
    isLoading: auditLoading,
    isError: auditError,
  } = useAuditLogs(undefined, { refetchInterval: REFETCH_INTERVAL });

  // Derived stats
  const services = servicesData?.services ?? [];
  const allCompilations: CompilationJobResponse[] = compilations ?? [];
  const auditEntries: AuditLogEntry[] = auditData?.entries ?? [];

  const totalServices = services.length;
  const totalCompilations = allCompilations.length;
  const publishedCount = allCompilations.filter(
    (c) => c.status === "succeeded",
  ).length;
  const failedCount = allCompilations.filter(
    (c) => c.status === "failed",
  ).length;
  const successRate =
    totalCompilations > 0
      ? Math.round(
          (publishedCount / (publishedCount + failedCount || 1)) * 100,
        )
      : 0;

  const totalTools = services.reduce(
    (sum, s) => sum + (s.tool_count ?? 0),
    0,
  );

  const apisHealthy = !servicesError && !compilationsError && !auditError;

  const recentCompilations = [...allCompilations]
    .sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    )
    .slice(0, 10);

  const recentAudit = [...auditEntries]
    .sort(
      (a, b) =>
        new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
    )
    .slice(0, 10);

  const anyLoading = servicesLoading || compilationsLoading || auditLoading;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          service2mcp overview
        </p>
      </div>

      {/* ── Stats Row ─────────────────────────────────────────── */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          icon={Server}
          title="Total Services"
          value={totalServices}
          subtitle={`${services.filter((s) => s.last_compiled).length} recently compiled`}
          loading={servicesLoading}
          error={servicesError}
        />
        <StatCard
          icon={ListChecks}
          title="Compilations"
          value={totalCompilations}
          subtitle={
            totalCompilations > 0
              ? `${successRate}% success rate`
              : "No compilations yet"
          }
          loading={compilationsLoading}
          error={compilationsError}
        />
        <StatCard
          icon={Wrench}
          title="Active Tools"
          value={totalTools}
          subtitle={`Across ${totalServices} services`}
          loading={servicesLoading}
          error={servicesError}
        />
        <StatCard
          icon={Activity}
          title="System Status"
          value={anyLoading ? "…" : apisHealthy ? "Healthy" : "Degraded"}
          subtitle={apisHealthy ? "All APIs responding" : "Some APIs unreachable"}
          loading={anyLoading}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ── Left: Recent Compilations + Activity ─────────── */}
        <div className="space-y-6 lg:col-span-2">
          {/* Recent Compilations */}
          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <div>
                <CardTitle className="text-base">
                  Recent Compilations
                </CardTitle>
                <CardDescription>
                  Last 10 compilation jobs
                </CardDescription>
              </div>
              <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/compilations" />}>
                  View All
                  <ArrowRight className="ml-1 h-3.5 w-3.5" />
              </Button>
            </CardHeader>
            <CardContent>
              {compilationsLoading ? (
                <div className="space-y-2">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <Skeleton key={i} className="h-10 w-full" />
                  ))}
                </div>
              ) : compilationsError ? (
                <p className="py-4 text-center text-sm text-muted-foreground">
                  Failed to load compilations.
                </p>
              ) : recentCompilations.length === 0 ? (
                <p className="py-4 text-center text-sm text-muted-foreground">
                  No compilations yet.{" "}
                  <Link
                    href="/compilations/new"
                    className="underline underline-offset-4 hover:text-foreground"
                  >
                    Start one →
                  </Link>
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-[100px]">Status</TableHead>
                        <TableHead>Job ID</TableHead>
                        <TableHead>Stage</TableHead>
                        <TableHead className="text-right">Time</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {recentCompilations.map((c) => (
                        <TableRow key={c.job_id}>
                          <TableCell>
                            <StatusBadge status={c.status} />
                          </TableCell>
                          <TableCell>
                            <Link
                              href={`/compilations/${c.job_id}`}
                              className="font-mono text-xs underline-offset-4 hover:underline"
                              title={c.job_id}
                            >
                              {truncateId(c.job_id)}
                            </Link>
                          </TableCell>
                          <TableCell className="text-sm">
                            {stageLabel(
                              c.current_stage ?? c.failed_stage,
                            )}
                          </TableCell>
                          <TableCell className="text-right text-xs text-muted-foreground">
                            {relativeTime(c.created_at)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Recent Activity */}
          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <div>
                <CardTitle className="text-base">Recent Activity</CardTitle>
                <CardDescription>Audit log timeline</CardDescription>
              </div>
              <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/audit" />}>
                  View All
                  <ArrowRight className="ml-1 h-3.5 w-3.5" />
              </Button>
            </CardHeader>
            <CardContent>
              {auditLoading ? (
                <div className="space-y-3">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-3">
                      <Skeleton className="h-7 w-7 rounded-full" />
                      <div className="flex-1 space-y-1">
                        <Skeleton className="h-3.5 w-3/4" />
                        <Skeleton className="h-3 w-1/2" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : auditError ? (
                <p className="py-4 text-center text-sm text-muted-foreground">
                  Failed to load audit logs.
                </p>
              ) : recentAudit.length === 0 ? (
                <p className="py-4 text-center text-sm text-muted-foreground">
                  No activity recorded yet.
                </p>
              ) : (
                <div className="space-y-3">
                  {recentAudit.map((entry, i) => (
                    <div key={entry.id}>
                      <div className="flex items-start gap-3">
                        {/* Actor initial */}
                        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium uppercase">
                          {entry.actor.charAt(0)}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex flex-wrap items-center gap-1.5 text-sm">
                            <span className="font-medium">{entry.actor}</span>
                            <Badge
                              variant="secondary"
                              className={`text-[10px] px-1.5 py-0 ${actionBadgeClass(entry.action)}`}
                            >
                              {entry.action}
                            </Badge>
                            <span className="truncate text-muted-foreground">
                              {entry.resource}
                            </span>
                          </div>
                          {entry.detail && (
                            <p className="mt-0.5 truncate text-xs text-muted-foreground">
                              {entry.detail}
                            </p>
                          )}
                        </div>
                        <span className="shrink-0 text-[11px] text-muted-foreground">
                          {relativeTime(entry.timestamp)}
                        </span>
                      </div>
                      {i < recentAudit.length - 1 && (
                        <Separator className="mt-3" />
                      )}
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* ── Right sidebar: Metrics + Quick Actions ──────── */}
        <div className="space-y-6">
          <CompilationMetrics />

          {/* Quick Actions */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Quick Actions</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2">
              <Button nativeButton={false} render={<Link href="/compilations/new" />}>
                New Compilation
              </Button>
              <Button variant="outline" nativeButton={false} render={<Link href="/services" />}>
                Browse Services
              </Button>
              <Button variant="outline" nativeButton={false} render={<Link href="/policies" />}>
                Manage Access
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
