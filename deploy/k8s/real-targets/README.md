# Real Protocol Test Target Services

Production-grade services for Tool Compiler v2 protocol testing. These replace the lightweight
mock targets in `../test-targets/` with real-world services that exercise auth, pagination,
schema introspection, nested schemas, error models, and health checks.

## Quick Start

```bash
# Full deploy (build custom images + apply manifests + wait for pods)
./deploy/k8s/real-targets/deploy-all.sh

# Apply-only (images already pushed)
./deploy/k8s/real-targets/deploy-all.sh --apply-only

# Seed mock data into all services
./deploy/k8s/real-targets/seed-all.sh --via-port-forward

# Teardown
./deploy/k8s/real-targets/deploy-all.sh --teardown
```

## Services

| Protocol | Service | Image | Port(s) | Cluster DNS |
|----------|---------|-------|---------|-------------|
| GraphQL+REST+OpenAPI | Directus 11 | directus/directus:11.4.1 | 8055 | `directus.tc-real-targets.svc.cluster.local:8055` |
| gRPC+HTTP | OpenFGA 1.8 | openfga/openfga:v1.8.2 | 8080,8081,3000 | `openfga.tc-real-targets.svc.cluster.local` |
| JSON-RPC | aria2 | custom (alpine+aria2) | 6800 | `aria2.tc-real-targets.svc.cluster.local:6800` |
| OData V4 | Northbreeze | custom (Python/Flask OData V4) | 4004 | `northbreeze.tc-real-targets.svc.cluster.local:4004` |
| OpenAPI | Gitea 1.22 | gitea/gitea:1.22 | 3000 | `gitea.tc-real-targets.svc.cluster.local:3000` |
| REST/JSON | PocketBase 0.25 | custom (alpine+pb) | 8090 | `pocketbase.tc-real-targets.svc.cluster.local:8090` |
| SCIM | BoxyHQ Jackson | boxyhq/jackson:latest (26.2.0) | 5225 | `jackson.tc-real-targets.svc.cluster.local:5225` |
| SOAP | Spring Boot CXF | custom (JRE 21) | 8080 | `soap-cxf.tc-real-targets.svc.cluster.local:8080` |
| SQL | PostgreSQL 16 | postgres:16-alpine | 5432 | `real-postgres.tc-real-targets.svc.cluster.local:5432` |

## Compiler Source URLs

```
GraphQL:   http://directus.tc-real-targets.svc.cluster.local:8055/graphql
REST:      http://directus.tc-real-targets.svc.cluster.local:8055/items/products
OpenAPI:   http://directus.tc-real-targets.svc.cluster.local:8055/server/specs/oas
gRPC:      openfga.tc-real-targets.svc.cluster.local:8081
JSON-RPC:  http://aria2.tc-real-targets.svc.cluster.local:6800/jsonrpc
OData:     http://northbreeze.tc-real-targets.svc.cluster.local:4004/odata/v4/northbreeze/$metadata
OpenAPI:   http://gitea.tc-real-targets.svc.cluster.local:3000/swagger.v1.json
REST:      http://pocketbase.tc-real-targets.svc.cluster.local:8090/api/collections/products/records
SCIM:      http://jackson.tc-real-targets.svc.cluster.local:5225/api/scim/v2.0/{tenant}/{product}
SOAP:      http://soap-cxf.tc-real-targets.svc.cluster.local:8080/services/OrderService?wsdl
SQL:       postgresql://catalog:catalog@real-postgres.tc-real-targets.svc.cluster.local:5432/catalog_v2
```

## Mock Data Summary

| Service | Data |
|---------|------|
| Directus | 3 collections (products×5, customers×4, orders×4) + auto GraphQL schema, auth, pagination |
| OpenFGA | Authorization model (user/org/document/folder), 11 relationship tuples |
| aria2 | Live JSON-RPC daemon with download management, session, version methods |
| Northbreeze | 15 products, 8 categories, 6 suppliers (Northwind-style), full OData V4 with $filter/$expand/$select/$count |
| Gitea | 3 users (admin+alice+bob), 1 org (acme-corp), 4 repos, 665KB Swagger spec |
| PocketBase | 2 collections (products×3, tasks×3), public read, admin auth for writes |
| Jackson | SCIM 2.0 directory sync — Users/Groups provisioning with API key auth |
| Spring CXF | 5 seed orders, 3 SOAP operations (GetOrderStatus, SubmitOrder, CancelOrder) + WSDL |
| PostgreSQL | Rich schema: 10 tables, 2 views, 1 function, 12 products, 8 customers, 10 orders, 12 reviews |

## Auth Credentials

| Service | Auth |
|---------|------|
| Directus | `admin@example.com` / `Admin123!` (JWT via `/auth/login`) |
| OpenFGA | No auth (memory store) |
| aria2 | RPC secret: `token:test-secret` |
| Gitea | `gitea_admin` / `Admin123!` (Basic auth or API token) |
| PocketBase | `admin@example.com` / `Admin12345!` (admin panel at `/_/`) |
| Jackson | No auth required for health; SCIM requires tenant/product setup |
| SOAP | No auth |
| PostgreSQL | `catalog` / `catalog` (database: `catalog_v2`); also `directus_user`/`directus_pass`, `jackson_user`/`jackson_pass` |

## Compiler Capability Coverage

| Capability | Exercised By |
|------------|-------------|
| Schema introspection | All services — each exposes discoverable schemas |
| Auth (JWT) | Directus, PocketBase |
| Auth (API key) | Jackson, aria2 |
| Auth (Basic) | Gitea |
| Pagination | Directus, Gitea, PocketBase, OData |
| Filtering | Directus, PocketBase, OData (OData V4 $filter) |
| Nested schemas | Directus (relations), OpenFGA (authorization model), SOAP (complex types) |
| Error models | SOAP (WSDL faults), OpenFGA (error codes), Gitea (HTTP error objects) |
| Server streaming | OpenFGA gRPC (ReadChanges) |
| Health checks | All services have readiness + liveness probes |
| OpenAPI spec | Directus (`/server/specs/oas`), Gitea (`/swagger.v1.json`) |
| WSDL | SOAP CXF (`/services/OrderService?wsdl`) |
| OData $metadata | Northbreeze (`/odata/v4/northbreeze/$metadata`) |
| gRPC reflection | OpenFGA (port 8081) |
| JSON-RPC discovery | aria2 (built-in method listing via `system.listMethods`) |

## Resource Footprint

| Service | CPU Request | Memory Request | CPU Limit | Memory Limit |
|---------|-------------|----------------|-----------|-------------|
| PostgreSQL | 50m | 128Mi | 500m | 384Mi |
| Directus | 50m | 256Mi | 500m | 512Mi |
| OpenFGA | 50m | 64Mi | 300m | 256Mi |
| aria2 | 30m | 32Mi | 200m | 128Mi |
| Northbreeze | 50m | 64Mi | 300m | 256Mi |
| Gitea | 50m | 128Mi | 500m | 384Mi |
| PocketBase | 30m | 32Mi | 200m | 128Mi |
| Jackson | 50m | 128Mi | 500m | 384Mi |
| SOAP CXF | 100m | 256Mi | 500m | 768Mi |
| **Total** | **460m** | **~1.1Gi** | **3.5** | **~3.2Gi** |

## Custom Images

Built and pushed to `us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/`:

| Image | Source |
|-------|--------|
| real-target-aria2 | aria2/ |
| real-target-northbreeze | northbreeze/ |
| real-target-pocketbase | pocketbase/ |
| real-target-soap-cxf | soap-cxf/ |

## Deployment Notes

- **Directus** needs ~3–5 min on first boot for DB migration. Startup probe allows up to 5 min.
- **Gitea** admin user must be created via `gitea admin user create` CLI after first deploy.
  The `seed-all.sh` script handles this automatically.
- **PocketBase** superuser is auto-created on startup via the entrypoint script.
- **Northbreeze** is a custom Python/Flask OData V4 service (original `ghcr.io/qmacro/northbreeze`
  image is private/403). Implements full OData V4: $metadata, $filter, $select, $top, $skip,
  $orderby, $count, $expand, navigation properties.
- **PostgreSQL** hosts 3 databases: `directus` (CMS), `jackson` (SCIM), `catalog_v2` (SQL testing).
  The `directus_user` must own the `directus` database/schema for Directus migrations to work.
