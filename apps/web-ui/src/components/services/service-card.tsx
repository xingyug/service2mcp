"use client";

import Link from "next/link";
import { Clock, Layers } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ProtocolBadge } from "@/components/services/protocol-badge";
import type { ServiceSummary } from "@/types/api";

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

interface ServiceCardProps {
  service: ServiceSummary;
}

export function ServiceCard({ service }: ServiceCardProps) {
  return (
    <Link href={`/services/${service.service_id}`}>
      <Card className="transition-shadow hover:shadow-md">
        <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0 pb-2">
          <div className="min-w-0 space-y-1">
            <ProtocolBadge protocol={service.protocol} size="sm" />
            <CardTitle className="truncate text-base">
              {service.name}
            </CardTitle>
          </div>
          {service.active_version != null && (
            <Badge variant="secondary" className="shrink-0">
              v{service.active_version}
            </Badge>
          )}
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <div className="flex items-center gap-4">
            <span className="flex items-center gap-1">
              <Layers className="size-3.5" />
              {service.version_count} version
              {service.version_count !== 1 ? "s" : ""}
            </span>
            <span className="flex items-center gap-1">
              <Clock className="size-3.5" />
              {relativeTime(service.last_compiled)}
            </span>
          </div>
          {(service.tenant || service.environment) && (
            <div className="flex flex-wrap gap-1">
              {service.tenant && (
                <Badge variant="outline" className="text-[10px]">
                  {service.tenant}
                </Badge>
              )}
              {service.environment && (
                <Badge variant="outline" className="text-[10px]">
                  {service.environment}
                </Badge>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </Link>
  );
}
