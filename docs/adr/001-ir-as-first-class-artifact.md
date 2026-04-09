# ADR 001: IR As First-Class Artifact

## Status

Accepted

## Context

service2mcp ingests heterogeneous upstream definitions such as OpenAPI, GraphQL, SQL schemas, and discovery-first REST surfaces. The platform also needs durable versioning, diffing, rollback, validation, and deployment metadata that survive any individual extractor or runtime implementation.

## Decision

`ServiceIR` is the primary compiled artifact of the platform. Every extractor must normalize upstream inputs into IR, and every downstream component must consume IR rather than extractor-specific payloads. Persisted service versions, validation reports, generated manifests, runtime loading, and diffs all attach to the IR boundary.

## Consequences

- Extractors stay protocol-specific, but downstream deployment and runtime logic stays protocol-agnostic.
- Version history, rollback, and semantic diffs operate on a stable contract instead of raw specs.
- IR model evolution becomes a high-impact compatibility surface and must remain versioned and validated.
