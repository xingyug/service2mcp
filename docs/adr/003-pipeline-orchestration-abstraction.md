# ADR 003: Pipeline Orchestration Abstraction With Temporal-Compatible Workflow Core

## Status

Accepted

## Context

The SDD identifies Temporal as the long-term durable workflow target, but the repository needed a working retryable state machine before operational commitment to Temporal. The current project phase prioritizes reliable local execution and a narrow, testable workflow core.

## Decision

The compilation pipeline is implemented as an engine-agnostic workflow core with explicit stage definitions, retries, rollback hooks, and persistence interfaces. Current deployment assumptions remain Celery/Redis-friendly, while the orchestration surface is intentionally compatible with a future Temporal binding.

## Consequences

- The current implementation can progress without blocking on Temporal cluster operations.
- Workflow semantics are explicit and testable outside any specific worker engine.
- A future Temporal migration still requires adapter work, but not a redesign of stage ordering, retries, or rollback behavior.
