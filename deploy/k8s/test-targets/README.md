# Protocol Test Target Services

Cluster-internal test services for every API protocol supported by Tool Compiler v2.
All services use **ClusterIP** only — zero external exposure, all traffic stays within the GKE VPC.

## Quick Start

```bash
# Full deploy (build images + apply manifests)
./deploy/k8s/test-targets/deploy-all.sh

# Apply-only (images already pushed)
./deploy/k8s/test-targets/deploy-all.sh --apply-only

# Teardown
./deploy/k8s/test-targets/deploy-all.sh --teardown
```

## Services

| Protocol | Service | Port | Cluster DNS |
|----------|---------|------|-------------|
| REST | rest-jsonserver | 3000 | `rest-jsonserver.tool-compiler-test-targets.svc.cluster.local:3000` |
| OpenAPI | openapi-petstore | 8080 | `openapi-petstore.tool-compiler-test-targets.svc.cluster.local:8080` |
| GraphQL | graphql-server | 4000 | `graphql-server.tool-compiler-test-targets.svc.cluster.local:4000` |
| gRPC | grpc-server | 50051 | `grpc-server.tool-compiler-test-targets.svc.cluster.local:50051` |
| SOAP | soap-server | 8000 | `soap-server.tool-compiler-test-targets.svc.cluster.local:8000` |
| SQL | sql-postgres | 5432 | `sql-postgres.tool-compiler-test-targets.svc.cluster.local:5432` |
| OData | odata-server | 8000 | `odata-server.tool-compiler-test-targets.svc.cluster.local:8000` |
| SCIM | scim-server | 8000 | `scim-server.tool-compiler-test-targets.svc.cluster.local:8000` |
| JSON-RPC | jsonrpc-server | 8000 | `jsonrpc-server.tool-compiler-test-targets.svc.cluster.local:8000` |

## Extractor Source URLs

Use these URLs when submitting compilation requests to Tool Compiler:

```
REST:      http://rest-jsonserver.tool-compiler-test-targets.svc.cluster.local:3000
OpenAPI:   http://openapi-petstore.tool-compiler-test-targets.svc.cluster.local:8080/api/v3/openapi.json
GraphQL:   http://graphql-server.tool-compiler-test-targets.svc.cluster.local:4000/graphql
gRPC:      grpc-server.tool-compiler-test-targets.svc.cluster.local:50051
SOAP:      http://soap-server.tool-compiler-test-targets.svc.cluster.local:8000/?wsdl
SQL:       postgresql://catalog:catalog@sql-postgres.tool-compiler-test-targets.svc.cluster.local:5432/catalog
OData:     http://odata-server.tool-compiler-test-targets.svc.cluster.local:8000/odata/$metadata
SCIM:      http://scim-server.tool-compiler-test-targets.svc.cluster.local:8000/scim/v2/Schemas
JSON-RPC:  http://jsonrpc-server.tool-compiler-test-targets.svc.cluster.local:8000/openrpc.json
```

## Mock Data Summary

| Service | Data |
|---------|------|
| REST | 6 collections × 3 items (posts, comments, albums, photos, todos, users) + nested routes |
| OpenAPI | Swagger PetStore v3 — 19 operations (pet, store, user) |
| GraphQL | Catalog schema — searchProducts query + adjustInventory mutation |
| gRPC | InventoryService — ListItems, AdjustInventory (unary), WatchInventory (server-stream) |
| SOAP | OrderService WSDL — GetOrderStatus, SubmitOrder |
| SQL | PostgreSQL — customers + orders tables, order_summaries view, 7 seed rows |
| OData | Products + Categories entity sets, GetTopProducts function, ResetProductData action |
| SCIM | User + Group schemas, full CRUD, SCIM 2.0 discovery endpoints |
| JSON-RPC | Calculator — add, subtract, get_history, delete_history + OpenRPC spec |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Namespace: tool-compiler-test-targets                  │
│                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐   │
│  │ REST :3000  │  │ OpenAPI     │  │ GraphQL      │   │
│  │ json-server │  │ PetStore    │  │ Apollo :4000 │   │
│  │             │  │ :8080       │  │              │   │
│  └─────────────┘  └─────────────┘  └──────────────┘   │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐   │
│  │ gRPC        │  │ SOAP :8000  │  │ SQL          │   │
│  │ :50051      │  │ Spyne WSDL  │  │ PG :5432     │   │
│  │ + reflection│  │             │  │              │   │
│  └─────────────┘  └─────────────┘  └──────────────┘   │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐   │
│  │ OData :8000 │  │ SCIM :8000  │  │ JSON-RPC     │   │
│  │ $metadata   │  │ /scim/v2    │  │ :8000 /rpc   │   │
│  └─────────────┘  └─────────────┘  └──────────────┘   │
│                                                         │
│  All services: ClusterIP only — no external access      │
└─────────────────────────────────────────────────────────┘
```

## Custom Images

Built and pushed to `us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/`:

| Image | Base | Source |
|-------|------|--------|
| test-target-rest | node:20-alpine | rest-jsonserver/ |
| test-target-graphql | node:20-alpine | graphql-server/ |
| test-target-grpc | python:3.12-slim | grpc-server/ |
| test-target-soap | python:3.11-slim | soap-server/ |
| test-target-odata | python:3.12-slim | odata-server/ |
| test-target-scim | python:3.12-slim | scim-server/ |
| test-target-jsonrpc | python:3.12-slim | jsonrpc-server/ |

Public images used directly: `swaggerapi/petstore3:unstable`, `postgres:16-alpine`.

## Resource Footprint

Each custom service: 50m CPU / 64Mi memory (request), 200m / 128Mi (limit).
PetStore (Java): 50m / 256Mi request, 500m / 512Mi limit.
PostgreSQL: 50m / 128Mi request, 500m / 256Mi limit.
Total cluster overhead: ~500m CPU, ~1GiB memory requested.
