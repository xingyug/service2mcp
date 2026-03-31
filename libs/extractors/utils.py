"""Shared utilities for extractors — no LLM calls (extractor purity)."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import httpx

from libs.extractors.base import SourceConfig

logger = logging.getLogger(__name__)


def slugify(text: str, *, camel_case: bool = False, default: str = "unnamed") -> str:
    """Convert text to URL-safe kebab-case slug.

    When *camel_case* is True, CamelCase word boundaries are split first
    (e.g. ``"MyService"`` → ``"my-service"``).
    """
    if camel_case:
        text = re.sub(r"(?<!^)(?=[A-Z])", "-", text)
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or default


def get_auth_headers(source: SourceConfig) -> dict[str, str]:
    """Build HTTP auth headers from *source* for spec/content fetching."""
    headers: dict[str, str] = {}
    if source.auth_header:
        headers["Authorization"] = source.auth_header
    elif source.auth_token:
        headers["Authorization"] = f"Bearer {source.auth_token}"
    return headers


def get_content(source: SourceConfig, *, timeout: float = 30.0) -> str | None:
    """Three-tier content fallback: file_content → file_path → url (HTTP GET)."""
    if source.file_content:
        return source.file_content
    if source.file_path:
        return Path(source.file_path).read_text(encoding="utf-8")
    if source.url:
        try:
            resp = httpx.get(
                source.url,
                timeout=timeout,
                headers=get_auth_headers(source),
            )
            resp.raise_for_status()
            return resp.text
        except (httpx.HTTPError, OSError):
            logger.warning("Failed to fetch content from %s", source.url, exc_info=True)
            return None
    return None


def compute_content_hash(content: str | bytes) -> str:
    """Return the SHA-256 hex digest of *content*."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()
