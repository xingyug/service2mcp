import type {
  ArtifactVersionResponse,
  GatewayRouteDocument,
  GatewayPreviousRoutes,
} from "@/types/api";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function sanitizeRouteComponent(value: string): string {
  const sanitized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

  if (!sanitized) {
    return "service";
  }

  return sanitized.slice(0, 63).replace(/-+$/g, "") || "service";
}

function routeIdentityBase(
  serviceId: string,
  tenant?: string,
  environment?: string,
): string {
  const segments = [sanitizeRouteComponent(serviceId)];
  if (tenant) {
    segments.push("tenant", sanitizeRouteComponent(tenant));
  }
  if (environment) {
    segments.push("env", sanitizeRouteComponent(environment));
  }
  return segments.join("-");
}

function buildRouteDocument(
  {
    routeId,
    routeType,
    serviceId,
    serviceName,
    tenant,
    environment,
    namespace,
    versionNumber,
    routeDefinition,
  }: {
    routeId: string;
    routeType: "default" | "version";
    serviceId: string;
    serviceName: string;
    tenant?: string;
    environment?: string;
    namespace: string;
    versionNumber: unknown;
    routeDefinition: Record<string, unknown>;
  },
): GatewayRouteDocument | undefined {
  const targetService = routeDefinition.target_service;
  if (!isRecord(targetService)) {
    return undefined;
  }

  const document: GatewayRouteDocument = {
    route_id: routeId,
    route_type: routeType,
    service_id: serviceId,
    service_name: serviceName,
    namespace,
    target_service: { ...targetService },
  };
  if (tenant) {
    document.tenant = tenant;
  }
  if (environment) {
    document.environment = environment;
  }

  if (typeof versionNumber === "number") {
    document.version_number = versionNumber;
  }
  if (typeof routeDefinition.switch_strategy === "string") {
    document.switch_strategy = routeDefinition.switch_strategy;
  }
  if (isRecord(routeDefinition.match)) {
    document.match = { ...routeDefinition.match };
  }

  return document;
}

export function buildRouteDocuments(
  routeConfig: Record<string, unknown>,
): GatewayPreviousRoutes {
  const serviceId = asString(routeConfig.service_id);
  const serviceName = asString(routeConfig.service_name);
  const namespace = asString(routeConfig.namespace);
  const tenant = asString(routeConfig.tenant);
  const environment = asString(routeConfig.environment);
  if (!serviceId || !serviceName || !namespace) {
    return {};
  }

  const previousRoutes: GatewayPreviousRoutes = {};
  const versionNumber = routeConfig.version_number;

  const defaultRoute = routeConfig.default_route;
  if (isRecord(defaultRoute)) {
    const routeId = `${routeIdentityBase(serviceId, tenant, environment)}-active`;
    const document = buildRouteDocument({
      routeId,
      routeType: "default",
      serviceId,
      serviceName,
      tenant,
      environment,
      namespace,
      versionNumber,
      routeDefinition: defaultRoute,
    });
    if (document) {
      previousRoutes[routeId] = document;
    }
  }

  const versionRoute = routeConfig.version_route;
  if (isRecord(versionRoute)) {
    const routeId =
      typeof versionNumber === "number"
        ? `${routeIdentityBase(serviceId, tenant, environment)}-v${versionNumber}`
        : asString(versionRoute.route_id);
    if (routeId) {
      const document = buildRouteDocument({
        routeId,
        routeType: "version",
        serviceId,
        serviceName,
        tenant,
        environment,
        namespace,
        versionNumber,
        routeDefinition: versionRoute,
      });
      if (document) {
        previousRoutes[routeId] = document;
      }
    }
  }

  return previousRoutes;
}

export function buildPreviousRoutes(
  routeConfig: Record<string, unknown>,
): GatewayPreviousRoutes {
  return buildRouteDocuments(routeConfig);
}

function normalizeJson(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(normalizeJson);
  }
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, normalizeJson(value[key])]),
    );
  }
  return value;
}

function routeDocumentsEqual(
  left: GatewayRouteDocument,
  right: GatewayRouteDocument,
): boolean {
  return JSON.stringify(normalizeJson(left)) === JSON.stringify(normalizeJson(right));
}

export function inferRouteStatus(
  routeConfig: Record<string, unknown> | undefined,
  gatewayRoutesById: Record<string, GatewayRouteDocument>,
): "synced" | "drifted" | "error" {
  if (!routeConfig) {
    return "error";
  }

  const expectedRoutes = Object.values(buildRouteDocuments(routeConfig));
  if (expectedRoutes.length === 0) {
    return "error";
  }

  const allRoutesMatch = expectedRoutes.every((expectedRoute) => {
    const gatewayRoute = gatewayRoutesById[expectedRoute.route_id];
    return gatewayRoute != null && routeDocumentsEqual(expectedRoute, gatewayRoute);
  });

  return allRoutesMatch ? "synced" : "drifted";
}

export function findArtifactVersion(
  versions: ArtifactVersionResponse[],
  versionNumber?: number,
): ArtifactVersionResponse | undefined {
  if (typeof versionNumber === "number" && Number.isFinite(versionNumber)) {
    return versions.find((version) => version.version_number === versionNumber);
  }
  return versions.find((version) => version.is_active);
}

export function findActiveArtifactVersion(
  versions: ArtifactVersionResponse[],
): ArtifactVersionResponse | undefined {
  return findArtifactVersion(versions);
}
