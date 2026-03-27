"use client";

import {
  Sparkles,
  FileJson,
  Globe,
  Share2,
  Database,
  Zap,
  FileCode,
} from "lucide-react";
import { cn } from "@/lib/utils";

const PROTOCOLS = [
  {
    value: "",
    label: "Auto-detect",
    description: "Automatically detect the API protocol",
    icon: Sparkles,
  },
  {
    value: "openapi",
    label: "OpenAPI",
    description: "OpenAPI / Swagger specification",
    icon: FileJson,
  },
  {
    value: "rest",
    label: "REST",
    description: "Generic REST API endpoint",
    icon: Globe,
  },
  {
    value: "graphql",
    label: "GraphQL",
    description: "GraphQL schema or endpoint",
    icon: Share2,
  },
  {
    value: "sql",
    label: "SQL",
    description: "SQL database interface",
    icon: Database,
  },
  {
    value: "grpc",
    label: "gRPC",
    description: "Protocol Buffers / gRPC service",
    icon: Zap,
  },
  {
    value: "soap",
    label: "SOAP",
    description: "SOAP / WSDL web service",
    icon: FileCode,
  },
] as const;

interface ProtocolSelectorProps {
  value: string;
  onChange: (value: string) => void;
}

export function ProtocolSelector({ value, onChange }: ProtocolSelectorProps) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {PROTOCOLS.map((proto) => {
        const Icon = proto.icon;
        const isSelected = value === proto.value;

        return (
          <button
            key={proto.value}
            type="button"
            onClick={() => onChange(proto.value)}
            className={cn(
              "flex flex-col items-center gap-2 rounded-lg border p-4 text-center transition-all hover:border-primary/50",
              isSelected
                ? "border-primary bg-primary/5 ring-2 ring-primary/20"
                : "border-border",
            )}
          >
            <Icon
              className={cn(
                "h-6 w-6",
                isSelected ? "text-primary" : "text-muted-foreground",
              )}
            />
            <div>
              <p className="text-sm font-medium">{proto.label}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {proto.description}
              </p>
            </div>
          </button>
        );
      })}
    </div>
  );
}
