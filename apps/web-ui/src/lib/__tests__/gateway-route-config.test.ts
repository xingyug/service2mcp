import { describe, expect, it } from "vitest";

import {
  buildRouteDocuments,
  buildPreviousRoutes,
  findActiveArtifactVersion,
  findArtifactVersion,
  inferRouteStatus,
} from "../gateway-route-config";

describe("gateway-route-config", () => {
  it("buildPreviousRoutes reconstructs default and version route documents", () => {
    const previousRoutes = buildPreviousRoutes({
      service_id: "billing-api",
      service_name: "Billing API",
      namespace: "runtime-system",
      version_number: 2,
      default_route: {
        route_id: "billing-api-active",
        target_service: {
          name: "billing-runtime-v2",
          namespace: "runtime-system",
          port: 8003,
        },
        switch_strategy: "atomic-upstream-swap",
      },
      version_route: {
        route_id: "billing-api-v2",
        target_service: {
          name: "billing-runtime-v2",
          namespace: "runtime-system",
          port: 8003,
        },
        match: {
          headers: {
            "x-tool-compiler-version": "2",
          },
        },
      },
    });

    expect(previousRoutes).toEqual({
      "billing-api-active": {
        route_id: "billing-api-active",
        route_type: "default",
        service_id: "billing-api",
        service_name: "Billing API",
        namespace: "runtime-system",
        version_number: 2,
        switch_strategy: "atomic-upstream-swap",
        target_service: {
          name: "billing-runtime-v2",
          namespace: "runtime-system",
          port: 8003,
        },
      },
      "billing-api-v2": {
        route_id: "billing-api-v2",
        route_type: "version",
        service_id: "billing-api",
        service_name: "Billing API",
        namespace: "runtime-system",
        version_number: 2,
        match: {
          headers: {
            "x-tool-compiler-version": "2",
          },
        },
        target_service: {
          name: "billing-runtime-v2",
          namespace: "runtime-system",
          port: 8003,
        },
      },
    });
  });

  it("buildPreviousRoutes skips malformed route configs", () => {
    expect(buildPreviousRoutes({ service_id: "svc-1" })).toEqual({});
  });

  it("buildRouteDocuments reconstructs current expected route documents", () => {
    const routeDocuments = buildRouteDocuments({
      service_id: "billing-api",
      service_name: "Billing API",
      namespace: "runtime-system",
      version_number: 2,
      default_route: {
        route_id: "billing-api-active",
        target_service: {
          name: "billing-runtime-v2",
          namespace: "runtime-system",
          port: 8003,
        },
      },
    });

    expect(routeDocuments).toEqual({
      "billing-api-active": {
        route_id: "billing-api-active",
        route_type: "default",
        service_id: "billing-api",
        service_name: "Billing API",
        namespace: "runtime-system",
        target_service: {
          name: "billing-runtime-v2",
          namespace: "runtime-system",
          port: 8003,
        },
        version_number: 2,
      },
    });
  });

  it("buildRouteDocuments canonicalizes scoped route ids and preserves scope", () => {
    const routeDocuments = buildRouteDocuments({
      service_id: "billing-api",
      service_name: "Billing API",
      tenant: "Team A",
      environment: "Prod",
      namespace: "runtime-system",
      version_number: 2,
      default_route: {
        route_id: "billing-api-active",
        target_service: {
          name: "billing-runtime-v2",
          namespace: "runtime-system",
          port: 8003,
        },
      },
      version_route: {
        route_id: "billing-api-v2",
        target_service: {
          name: "billing-runtime-v2",
          namespace: "runtime-system",
          port: 8003,
        },
        match: {
          headers: {
            "x-tool-compiler-version": "2",
          },
        },
      },
    });

    expect(routeDocuments).toEqual({
      "billing-api-tenant-team-a-env-prod-active": {
        route_id: "billing-api-tenant-team-a-env-prod-active",
        route_type: "default",
        service_id: "billing-api",
        service_name: "Billing API",
        tenant: "Team A",
        environment: "Prod",
        namespace: "runtime-system",
        target_service: {
          name: "billing-runtime-v2",
          namespace: "runtime-system",
          port: 8003,
        },
        version_number: 2,
      },
      "billing-api-tenant-team-a-env-prod-v2": {
        route_id: "billing-api-tenant-team-a-env-prod-v2",
        route_type: "version",
        service_id: "billing-api",
        service_name: "Billing API",
        tenant: "Team A",
        environment: "Prod",
        namespace: "runtime-system",
        target_service: {
          name: "billing-runtime-v2",
          namespace: "runtime-system",
          port: 8003,
        },
        version_number: 2,
        match: {
          headers: {
            "x-tool-compiler-version": "2",
          },
        },
      },
    });
  });

  it("inferRouteStatus returns synced when gateway routes match current config", () => {
    expect(
      inferRouteStatus(
        {
          service_id: "billing-api",
          service_name: "Billing API",
          namespace: "runtime-system",
          version_number: 2,
          default_route: {
            route_id: "billing-api-active",
            target_service: {
              name: "billing-runtime-v2",
              namespace: "runtime-system",
              port: 8003,
            },
          },
        },
        {
          "billing-api-active": {
            route_id: "billing-api-active",
            route_type: "default",
            service_id: "billing-api",
            service_name: "Billing API",
            namespace: "runtime-system",
            target_service: {
              name: "billing-runtime-v2",
              namespace: "runtime-system",
              port: 8003,
            },
            version_number: 2,
          },
        },
      ),
    ).toBe("synced");
  });

  it("inferRouteStatus returns drifted when gateway routes differ", () => {
    expect(
      inferRouteStatus(
        {
          service_id: "billing-api",
          service_name: "Billing API",
          namespace: "runtime-system",
          version_number: 2,
          default_route: {
            route_id: "billing-api-active",
            target_service: {
              name: "billing-runtime-v2",
              namespace: "runtime-system",
              port: 8003,
            },
          },
        },
        {
          "billing-api-active": {
            route_id: "billing-api-active",
            route_type: "default",
            service_id: "billing-api",
            service_name: "Billing API",
            namespace: "runtime-system",
            target_service: {
              name: "billing-runtime-v1",
              namespace: "runtime-system",
              port: 8003,
            },
            version_number: 1,
          },
        },
      ),
    ).toBe("drifted");
  });

  it("findArtifactVersion picks explicit version when provided", () => {
    const versions = [
      {
        service_id: "billing-api",
        version_number: 1,
        is_active: false,
        created_at: "2026-03-29T00:00:00Z",
        ir: {} as never,
      },
      {
        service_id: "billing-api",
        version_number: 2,
        is_active: true,
        created_at: "2026-03-29T01:00:00Z",
        ir: {} as never,
        route_config: { service_id: "billing-api" },
      },
    ];

    expect(findArtifactVersion(versions, 1)?.version_number).toBe(1);
    expect(findActiveArtifactVersion(versions)?.version_number).toBe(2);
  });
});
