import type { ServiceScope } from "@/types/api";

function scopeSegments(scope?: ServiceScope) {
  return [scope?.tenant ?? null, scope?.environment ?? null] as const;
}

export const queryKeys = {
  compilations: {
    all: ["compilations"] as const,
    detail: (id: string) => ["compilations", id] as const,
  },

  services: {
    all: ["services"] as const,
    filtered: (filters: Record<string, string>) =>
      ["services", filters] as const,
    detail: (id: string, scope?: ServiceScope) =>
      ["services", id, "detail", ...scopeSegments(scope)] as const,
    tools: (id: string, scope?: ServiceScope) =>
      ["services", id, "tools", ...scopeSegments(scope)] as const,
  },

  artifacts: {
    versions: (serviceId: string, scope?: ServiceScope) =>
      ["artifacts", serviceId, "versions", ...scopeSegments(scope)] as const,
    version: (serviceId: string, version: number, scope?: ServiceScope) =>
      ["artifacts", serviceId, "versions", version, ...scopeSegments(scope)] as const,
    diff: (
      serviceId: string,
      from: number,
      to: number,
      scope?: ServiceScope,
    ) => ["artifacts", serviceId, "diff", from, to, ...scopeSegments(scope)] as const,
  },

  policies: {
    all: ["policies"] as const,
    filtered: (filters: Record<string, string>) =>
      ["policies", filters] as const,
    detail: (id: string) => ["policies", id] as const,
  },

  audit: {
    logs: (filters: Record<string, string>) =>
      ["audit", "logs", filters] as const,
  },

  auth: {
    pats: ["auth", "pats"] as const,
  },
};
