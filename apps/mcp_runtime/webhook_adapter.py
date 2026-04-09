"""Webhook registration and payload collection adapter."""

from __future__ import annotations

import logging
import uuid
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_STORED = 100


class WebhookAdapter:
    """Manages webhook registration and payload collection."""

    def __init__(self, callback_base_url: str | None = None) -> None:
        self._callback_base_url = callback_base_url
        self._payloads: dict[str, deque[dict[str, Any]]] = {}
        self._registered: dict[str, dict[str, Any]] = {}

    def register(self, channel: str, target_url: str | None = None) -> dict[str, Any]:
        """Register a webhook endpoint for a channel."""
        callback_url = target_url
        if callback_url is None and self._callback_base_url:
            callback_url = f"{self._callback_base_url.rstrip('/')}/webhooks/{channel}"

        registration_id = uuid.uuid4().hex[:12]
        self._registered[channel] = {
            "registration_id": registration_id,
            "channel": channel,
            "callback_url": callback_url,
        }
        self._payloads.setdefault(channel, deque(maxlen=_DEFAULT_MAX_STORED))
        logger.info("Webhook registered: channel=%s callback_url=%s", channel, callback_url)
        return self._registered[channel]

    def deregister(self, channel: str) -> dict[str, Any]:
        """Remove a webhook registration."""
        registration = self._registered.pop(channel, None)
        if registration is None:
            return {"channel": channel, "status": "not_found"}
        self._payloads.pop(channel, None)
        logger.info("Webhook deregistered: channel=%s", channel)
        return {"channel": channel, "status": "deregistered"}

    def receive_payload(self, channel: str, payload: dict[str, Any]) -> None:
        """Store an incoming webhook payload for retrieval."""
        buf = self._payloads.setdefault(channel, deque(maxlen=_DEFAULT_MAX_STORED))
        buf.append(payload)
        logger.debug("Webhook payload received: channel=%s", channel)

    def get_payloads(self, channel: str, max_count: int = 10) -> list[dict[str, Any]]:
        """Retrieve collected payloads for a channel (FIFO)."""
        buf = self._payloads.get(channel)
        if not buf:
            return []
        results: list[dict[str, Any]] = []
        while buf and len(results) < max_count:
            results.append(buf.popleft())
        return results

    def list_registrations(self) -> dict[str, dict[str, Any]]:
        """Return all active webhook registrations."""
        return dict(self._registered)
