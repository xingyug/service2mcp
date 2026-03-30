import type { ServiceScope } from "@/types/api";

type SearchParamsReader = {
  get(name: string): string | null;
};

type ScopedServiceRef = ServiceScope & {
  service_id: string;
};

export function normalizeServiceScope(
  scope?: ServiceScope | null,
): ServiceScope | undefined {
  const tenant = scope?.tenant?.trim() || undefined;
  const environment = scope?.environment?.trim() || undefined;

  if (!tenant && !environment) {
    return undefined;
  }

  return { tenant, environment };
}

export function serviceScopeFromSearchParams(
  searchParams: SearchParamsReader,
): ServiceScope | undefined {
  return normalizeServiceScope({
    tenant: searchParams.get("tenant") ?? undefined,
    environment: searchParams.get("environment") ?? undefined,
  });
}

export function serviceScopeSearchParams(
  scope?: ServiceScope | null,
): URLSearchParams {
  const params = new URLSearchParams();
  const normalized = normalizeServiceScope(scope);

  if (!normalized) {
    return params;
  }

  if (normalized.tenant) {
    params.set("tenant", normalized.tenant);
  }
  if (normalized.environment) {
    params.set("environment", normalized.environment);
  }

  return params;
}

export function appendServiceScope(
  path: string,
  scope?: ServiceScope | null,
): string {
  const params = serviceScopeSearchParams(scope);
  const query = params.toString();

  if (!query) {
    return path;
  }

  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}${query}`;
}

export function buildServiceDetailHref(
  service: ScopedServiceRef | string,
  scope?: ServiceScope | null,
): string {
  if (typeof service === "string") {
    return appendServiceScope(`/services/${service}`, scope);
  }

  return appendServiceScope(`/services/${service.service_id}`, service);
}

export function buildScopedServiceKey(service: ScopedServiceRef): string {
  const normalized = normalizeServiceScope(service);
  return [
    service.service_id,
    normalized?.tenant ?? "",
    normalized?.environment ?? "",
  ].join("::");
}
