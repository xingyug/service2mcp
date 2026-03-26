# ADR 002: Generic Runtime Default

## Status

Accepted

## Context

The platform must onboard many APIs quickly without paying a bespoke code-generation and image-build cost for every service. Most upstream APIs can be represented as HTTP-style operations with structured parameters, auth metadata, and response constraints.

## Decision

The default runtime is a single generic MCP server image that loads `ServiceIR` at startup, registers enabled tools dynamically, and proxies calls to the upstream service. Code generation remains an escape hatch for future edge cases, but not the baseline path.

## Consequences

- Most services can be deployed by shipping IR and generic manifests instead of generating per-service code.
- Runtime behavior becomes easier to patch consistently across all onboarded services.
- Performance-sensitive or protocol-specific edge cases may eventually need targeted fast paths or generated runtimes.
