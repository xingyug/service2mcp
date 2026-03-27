export const queryKeys = {
  compilations: {
    all: ["compilations"] as const,
    detail: (id: string) => ["compilations", id] as const,
  },

  services: {
    all: ["services"] as const,
    filtered: (filters: Record<string, string>) =>
      ["services", filters] as const,
    detail: (id: string) => ["services", id] as const,
    tools: (id: string) => ["services", id, "tools"] as const,
  },

  artifacts: {
    versions: (serviceId: string) =>
      ["artifacts", serviceId, "versions"] as const,
    version: (serviceId: string, version: number) =>
      ["artifacts", serviceId, "versions", version] as const,
    diff: (serviceId: string, from: number, to: number) =>
      ["artifacts", serviceId, "diff", from, to] as const,
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
