"use client";

import { useMemo } from "react";
import { BarChart3, TrendingUp } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useCompilations } from "@/hooks/use-api";
import type { CompilationJobResponse, CompilationStatus } from "@/types/api";

// ── Protocol distribution colours ──────────────────────────────────────────
const PROTOCOL_COLORS: Record<string, string> = {
  openapi: "bg-blue-500",
  rest: "bg-indigo-500",
  graphql: "bg-pink-500",
  sql: "bg-amber-500",
  grpc: "bg-teal-500",
  soap: "bg-orange-500",
  unknown: "bg-gray-400",
};

// ── Status category colours ────────────────────────────────────────────────
const STATUS_CATEGORIES: {
  key: string;
  label: string;
  color: string;
  match: (s: CompilationStatus) => boolean;
}[] = [
  {
    key: "published",
    label: "Published",
    color: "bg-green-500",
    match: (s) => s === "PUBLISHED",
  },
  {
    key: "failed",
    label: "Failed",
    color: "bg-red-500",
    match: (s) => s === "FAILED",
  },
  {
    key: "in_progress",
    label: "In Progress",
    color: "bg-blue-500",
    match: (s) =>
      ![
        "PUBLISHED",
        "FAILED",
        "PENDING",
        "ROLLED_BACK",
        "ROLLING_BACK",
      ].includes(s),
  },
  {
    key: "pending",
    label: "Pending",
    color: "bg-gray-400",
    match: (s) => s === "PENDING",
  },
  {
    key: "rolled_back",
    label: "Rolled Back",
    color: "bg-yellow-500",
    match: (s) => s === "ROLLED_BACK" || s === "ROLLING_BACK",
  },
];

// ── Helpers ────────────────────────────────────────────────────────────────

function buildProtocolDistribution() {
  // We don't have protocol on CompilationJobResponse, but we can derive it
  // from the source_url or options – for now we'll count by status distribution.
  // Actually the compilations don't have protocol in the list endpoint.
  // We'll use a stub approach – group by the first letter of job_id as placeholder,
  // but in practice the dashboard should be fed by service data.
  // Re-read: the spec says "All data derived from the compilations list API"
  // Since CompilationJobResponse has no protocol field, we return empty.
  return new Map<string, number>();
}

function buildStatusDistribution(compilations: CompilationJobResponse[]) {
  const counts = new Map<string, number>();
  for (const cat of STATUS_CATEGORIES) {
    counts.set(cat.key, 0);
  }
  for (const c of compilations) {
    for (const cat of STATUS_CATEGORIES) {
      if (cat.match(c.status)) {
        counts.set(cat.key, (counts.get(cat.key) ?? 0) + 1);
        break;
      }
    }
  }
  return counts;
}

function buildDailyTrend(compilations: CompilationJobResponse[]) {
  const now = new Date();
  const days: { label: string; count: number }[] = [];

  for (let i = 6; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    const dayStr = d.toISOString().slice(0, 10);
    const label = d.toLocaleDateString("en-US", { weekday: "short" });
    const count = compilations.filter(
      (c) => c.created_at.slice(0, 10) === dayStr,
    ).length;
    days.push({ label, count });
  }
  return days;
}

// ── Component ──────────────────────────────────────────────────────────────

export function CompilationMetrics() {
  const { data: compilationsData, isLoading } = useCompilations();

  const { protocolDist, statusDist, trend, maxTrend, totalCompilations } =
    useMemo(() => {
      const items = compilationsData ?? [];
      const pd = buildProtocolDistribution();
      const sd = buildStatusDistribution(items);
      const t = buildDailyTrend(items);
      const mx = Math.max(...t.map((d) => d.count), 1);
      return {
        protocolDist: pd,
        statusDist: sd,
        trend: t,
        maxTrend: mx,
        totalCompilations: items.length,
      };
    }, [compilationsData]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-4 w-56" />
        </CardHeader>
        <CardContent className="space-y-6">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    );
  }

  const statusEntries = STATUS_CATEGORIES.map((cat) => ({
    ...cat,
    count: statusDist.get(cat.key) ?? 0,
  })).filter((e) => e.count > 0);

  const protocolEntries = Array.from(protocolDist.entries())
    .map(([protocol, count]) => ({ protocol, count }))
    .sort((a, b) => b.count - a.count);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <BarChart3 className="h-4 w-4" />
          Compilation Metrics
        </CardTitle>
        <CardDescription>
          Overview of {totalCompilations} total compilations
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* ── Status Distribution ─────────────────────────────── */}
        <div>
          <p className="mb-2 text-sm font-medium">Status Distribution</p>
          {totalCompilations === 0 ? (
            <p className="text-xs text-muted-foreground">No compilations yet</p>
          ) : (
            <>
              {/* Segmented bar */}
              <div className="flex h-4 w-full overflow-hidden rounded-full">
                {statusEntries.map((e) => (
                  <div
                    key={e.key}
                    className={`${e.color} transition-all`}
                    style={{
                      width: `${(e.count / totalCompilations) * 100}%`,
                    }}
                  />
                ))}
              </div>
              {/* Legend */}
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                {statusEntries.map((e) => (
                  <div
                    key={e.key}
                    className="flex items-center gap-1.5 text-xs text-muted-foreground"
                  >
                    <span
                      className={`inline-block h-2 w-2 rounded-full ${e.color}`}
                    />
                    {e.label} ({e.count})
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        {/* ── Protocol Distribution ───────────────────────────── */}
        {protocolEntries.length > 0 && (
          <div>
            <p className="mb-2 text-sm font-medium">Protocol Distribution</p>
            <div className="space-y-2">
              {protocolEntries.map(({ protocol, count }) => (
                <div key={protocol}>
                  <div className="mb-0.5 flex items-center justify-between text-xs">
                    <span className="capitalize">{protocol}</span>
                    <span className="text-muted-foreground">{count}</span>
                  </div>
                  <div className="h-2 w-full rounded-full bg-muted">
                    <div
                      className={`h-full rounded-full transition-all ${PROTOCOL_COLORS[protocol] ?? PROTOCOL_COLORS.unknown}`}
                      style={{
                        width: `${(count / totalCompilations) * 100}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── 7-Day Trend ─────────────────────────────────────── */}
        <div>
          <p className="mb-2 flex items-center gap-1.5 text-sm font-medium">
            <TrendingUp className="h-3.5 w-3.5" />
            Last 7 Days
          </p>
          <div className="flex items-end gap-1.5">
            {trend.map((day, i) => (
              <div key={i} className="flex flex-1 flex-col items-center gap-1">
                <span className="text-[10px] text-muted-foreground">
                  {day.count > 0 ? day.count : ""}
                </span>
                <div
                  className="w-full rounded-sm bg-primary/80 transition-all"
                  style={{
                    height: `${Math.max((day.count / maxTrend) * 48, day.count > 0 ? 4 : 2)}px`,
                    opacity: day.count > 0 ? 1 : 0.2,
                  }}
                />
                <span className="text-[10px] text-muted-foreground">
                  {day.label}
                </span>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
