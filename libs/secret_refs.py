"""Shared helpers for resolving secret references from the environment."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

_NORMALIZED_SECRET_REF_PATTERN = re.compile(r"\W+")
_KUBERNETES_SECRET_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class MissingSecretReferenceError(LookupError):
    """Raised when a referenced secret cannot be resolved from the environment."""

    def __init__(self, *, secret_ref: str, purpose: str, context: str) -> None:
        super().__init__(f"Missing {purpose} for {context}: {secret_ref}.")
        self.secret_ref = secret_ref
        self.purpose = purpose
        self.context = context


class SecretReferenceCollisionError(ValueError):
    """Raised when multiple secret refs normalize to the same env-var name."""

    def __init__(self, *, context: str, collisions: dict[str, tuple[str, ...]]) -> None:
        details = "; ".join(
            f"{normalized} <- {', '.join(refs)}"
            for normalized, refs in sorted(collisions.items())
        )
        super().__init__(
            f"Secret refs for {context} normalize to the same env name: {details}."
        )
        self.context = context
        self.collisions = collisions


def normalized_secret_ref_name(secret_ref: str) -> str:
    """Return the normalized env-var-friendly name for a secret reference."""

    return _NORMALIZED_SECRET_REF_PATTERN.sub("_", secret_ref).upper()


def kubernetes_secret_key_name(secret_ref: str) -> str:
    """Return a Kubernetes Secret key name for a secret reference."""

    if _KUBERNETES_SECRET_KEY_PATTERN.fullmatch(secret_ref):
        return secret_ref
    return normalized_secret_ref_name(secret_ref)


def find_secret_ref_name_collisions(secret_refs: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """Group secret refs that would collide after env-name normalization."""

    grouped: dict[str, list[str]] = {}
    for secret_ref in secret_refs:
        normalized = normalized_secret_ref_name(secret_ref)
        refs = grouped.setdefault(normalized, [])
        if secret_ref not in refs:
            refs.append(secret_ref)
    return {
        normalized: tuple(refs)
        for normalized, refs in grouped.items()
        if len(refs) > 1
    }


def ensure_no_secret_ref_name_collisions(secret_refs: Sequence[str], *, context: str) -> None:
    """Fail fast when distinct secret refs would alias to the same env name."""

    collisions = find_secret_ref_name_collisions(secret_refs)
    if collisions:
        raise SecretReferenceCollisionError(context=context, collisions=collisions)


def candidate_env_names(secret_ref: str) -> list[str]:
    """Return candidate environment variable names for a secret reference."""

    normalized = normalized_secret_ref_name(secret_ref)
    candidates = [secret_ref]
    if normalized not in candidates:
        candidates.append(normalized)
    return candidates


def resolve_secret_ref(secret_ref: str, *, purpose: str, context: str) -> str:
    """Resolve a secret reference from the current environment."""

    for env_name in candidate_env_names(secret_ref):
        secret = os.getenv(env_name)
        if secret:
            return secret

    raise MissingSecretReferenceError(
        secret_ref=secret_ref,
        purpose=purpose,
        context=context,
    )
