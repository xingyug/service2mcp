# ADR 004: OIDC/JWT-Compatible Auth With Personal Access Tokens

## Status

Accepted

## Context

Compiled tool services need both user-oriented authentication and machine-to-machine access. The access-control layer also has to support API gateway synchronization, local development, and auditability without introducing a full external identity provider in the first iteration.

## Decision

The access-control service accepts JWT-based bearer authentication and manages first-party personal access tokens backed by PostgreSQL. The service is designed to remain OIDC-compatible at the token-validation boundary, while PAT issuance and revocation stay under platform control.

## Consequences

- Human and automation access can share one authorization and audit path.
- Local and test environments do not depend on an external OIDC server to exercise the core access model.
- Production deployments still need careful JWT issuer, audience, and key-management configuration.
