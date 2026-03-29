import {
  Braces,
  Globe,
  Code,
  Database,
  Network,
  Shield,
  Zap,
  FileText,
  Server,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

const protocolConfig: Record<
  string,
  { label: string; icon: LucideIcon; color: string }
> = {
  openapi: {
    label: "OpenAPI",
    icon: Globe,
    color: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  },
  rest: {
    label: "REST",
    icon: Server,
    color:
      "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  },
  graphql: {
    label: "GraphQL",
    icon: Code,
    color: "bg-pink-100 text-pink-700 dark:bg-pink-900/40 dark:text-pink-300",
  },
  grpc: {
    label: "gRPC",
    icon: Zap,
    color:
      "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300",
  },
  jsonrpc: {
    label: "JSON-RPC",
    icon: Braces,
    color:
      "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  },
  odata: {
    label: "OData",
    icon: Network,
    color: "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300",
  },
  scim: {
    label: "SCIM",
    icon: Shield,
    color: "bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300",
  },
  soap: {
    label: "SOAP",
    icon: FileText,
    color:
      "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
  },
  sql: {
    label: "SQL",
    icon: Database,
    color: "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-300",
  },
};

const fallback = {
  label: "Unknown",
  icon: Server,
  color: "bg-muted text-muted-foreground",
};

interface ProtocolBadgeProps {
  protocol: string;
  size?: "sm" | "md";
  className?: string;
}

export function ProtocolBadge({
  protocol,
  size = "md",
  className,
}: ProtocolBadgeProps) {
  const config = protocolConfig[protocol.toLowerCase()] ?? fallback;
  const Icon = config.icon;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full font-medium",
        size === "sm" ? "px-2 py-0.5 text-[10px]" : "px-2.5 py-0.5 text-xs",
        config.color,
        className,
      )}
    >
      <Icon className={size === "sm" ? "size-3" : "size-3.5"} />
      {config.label}
    </span>
  );
}
