# ADR 005: Semantic Risk Classification

## Status

Accepted

## Context

HTTP verbs alone do not reliably express operational risk. Tool invocation policies, default enablement, and runtime safeguards need a stronger model than simply mapping `GET` to safe and everything else to write access.

## Decision

Each IR operation carries explicit semantic risk metadata, including whether it writes state, is destructive, triggers external side effects, or is idempotent. Authorization decisions and default enablement rules key off this semantic risk model instead of raw transport metadata alone.

## Consequences

- Access control can reason about business risk rather than protocol trivia.
- Extractors and enhancers must preserve and validate risk metadata carefully.
- Unknown-risk operations require conservative defaults and explicit review to become callable.
