"use client";

import { useState, useCallback } from "react";
import { useTheme } from "next-themes";
import { ExternalLink } from "lucide-react";

import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

const GRAFANA_URL =
  process.env.NEXT_PUBLIC_GRAFANA_URL || "http://localhost:3000";

const DASHBOARDS = [
  {
    id: "compilation",
    label: "Compilation",
    path: "/d/compilation/compilation-dashboard",
  },
  {
    id: "runtime",
    label: "Runtime",
    path: "/d/runtime/runtime-dashboard",
  },
  {
    id: "access-control",
    label: "Access Control",
    path: "/d/access-control/access-control-dashboard",
  },
] as const;

function GrafanaIframe({
  dashboardPath,
  theme,
}: {
  dashboardPath: string;
  theme: string;
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const grafanaTheme = theme === "dark" ? "dark" : "light";
  const src = `${GRAFANA_URL}${dashboardPath}?orgId=1&theme=${grafanaTheme}&kiosk`;

  const handleLoad = useCallback(() => setLoading(false), []);
  const handleError = useCallback(() => {
    setLoading(false);
    setError(true);
  }, []);

  if (error) {
    return <GrafanaUnavailable />;
  }

  return (
    <div className="relative w-full" style={{ height: "calc(100vh - 200px)" }}>
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="space-y-4 w-full max-w-md text-center">
            <Skeleton className="mx-auto h-8 w-48" />
            <Skeleton className="mx-auto h-4 w-64" />
            <Skeleton className="h-64 w-full" />
          </div>
        </div>
      )}
      <iframe
        src={src}
        className="h-full w-full rounded-md border-0"
        onLoad={handleLoad}
        onError={handleError}
        title="Grafana Dashboard"
        allow="fullscreen"
      />
    </div>
  );
}

function GrafanaUnavailable() {
  return (
    <Card className="mx-auto max-w-lg">
      <CardHeader>
        <CardTitle>Grafana Not Configured</CardTitle>
        <CardDescription>
          Unable to connect to Grafana at{" "}
          <code className="text-xs">{GRAFANA_URL}</code>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm text-muted-foreground">
        <p>To enable observability dashboards:</p>
        <ol className="list-inside list-decimal space-y-1">
          <li>
            Ensure Grafana is running and accessible at the configured URL.
          </li>
          <li>
            Set <code className="text-xs">NEXT_PUBLIC_GRAFANA_URL</code> to
            your Grafana instance URL.
          </li>
          <li>
            Import the pre-built Tool Compiler dashboards into Grafana.
          </li>
          <li>
            Enable anonymous access or configure auth for iframe embedding.
          </li>
        </ol>
      </CardContent>
    </Card>
  );
}

export default function ObservePage() {
  const { resolvedTheme } = useTheme();
  const currentTheme = resolvedTheme ?? "light";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Observability Dashboards</h1>
          <p className="text-sm text-muted-foreground">
            Monitor system metrics and performance via Grafana.
          </p>
        </div>
      </div>

      <Tabs defaultValue="compilation">
        <div className="flex items-center justify-between">
          <TabsList>
            {DASHBOARDS.map((d) => (
              <TabsTrigger key={d.id} value={d.id}>
                {d.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>

        {DASHBOARDS.map((d) => (
          <TabsContent key={d.id} value={d.id} className="mt-4">
            <div className="mb-2 flex justify-end">
              <Button
                variant="outline"
                size="sm"
                render={
                  <a
                    href={`${GRAFANA_URL}${d.path}`}
                    target="_blank"
                    rel="noopener noreferrer"
                  />
                }
              >
                Open in Grafana
                <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
              </Button>
            </div>
            <GrafanaIframe dashboardPath={d.path} theme={currentTheme} />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
