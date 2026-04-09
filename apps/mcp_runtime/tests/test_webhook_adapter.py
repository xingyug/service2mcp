"""Unit tests for the webhook adapter."""

from __future__ import annotations

import pytest

from apps.mcp_runtime.webhook_adapter import WebhookAdapter


@pytest.fixture()
def adapter() -> WebhookAdapter:
    return WebhookAdapter(callback_base_url="https://hooks.example.com")


class TestWebhookAdapter:
    def test_register_webhook(self, adapter: WebhookAdapter) -> None:
        result = adapter.register("orders/new")
        assert "registration_id" in result
        assert result["channel"] == "orders/new"
        assert result["callback_url"] == "https://hooks.example.com/webhooks/orders/new"

    def test_register_webhook_custom_url(self, adapter: WebhookAdapter) -> None:
        result = adapter.register("ch", target_url="https://custom.url/hook")
        assert result["callback_url"] == "https://custom.url/hook"

    def test_deregister_webhook(self, adapter: WebhookAdapter) -> None:
        adapter.register("events/click")
        result = adapter.deregister("events/click")
        assert result["status"] == "deregistered"
        assert result["channel"] == "events/click"

    def test_receive_and_get_payloads(self, adapter: WebhookAdapter) -> None:
        adapter.register("ch1")
        adapter.receive_payload("ch1", {"event": "a"})
        adapter.receive_payload("ch1", {"event": "b"})
        payloads = adapter.get_payloads("ch1")
        assert len(payloads) == 2
        assert payloads[0] == {"event": "a"}
        assert payloads[1] == {"event": "b"}

    def test_get_payloads_fifo_order(self, adapter: WebhookAdapter) -> None:
        adapter.register("fifo")
        for i in range(5):
            adapter.receive_payload("fifo", {"seq": i})
        payloads = adapter.get_payloads("fifo", max_count=3)
        assert [p["seq"] for p in payloads] == [0, 1, 2]
        # remaining payloads still available
        remaining = adapter.get_payloads("fifo")
        assert [p["seq"] for p in remaining] == [3, 4]

    def test_get_payloads_max_count(self, adapter: WebhookAdapter) -> None:
        adapter.register("limited")
        for i in range(10):
            adapter.receive_payload("limited", {"n": i})
        payloads = adapter.get_payloads("limited", max_count=3)
        assert len(payloads) == 3

    def test_list_registrations(self, adapter: WebhookAdapter) -> None:
        adapter.register("a")
        adapter.register("b")
        regs = adapter.list_registrations()
        assert "a" in regs
        assert "b" in regs
        assert regs["a"]["channel"] == "a"

    def test_deregister_nonexistent(self, adapter: WebhookAdapter) -> None:
        result = adapter.deregister("does-not-exist")
        assert result["status"] == "not_found"
        assert result["channel"] == "does-not-exist"

    def test_get_payloads_empty_channel(self, adapter: WebhookAdapter) -> None:
        payloads = adapter.get_payloads("nonexistent")
        assert payloads == []

    def test_register_no_base_url(self) -> None:
        adapter = WebhookAdapter()
        result = adapter.register("ch")
        assert result["callback_url"] is None

    def test_deregister_clears_payloads(self, adapter: WebhookAdapter) -> None:
        adapter.register("ch")
        adapter.receive_payload("ch", {"x": 1})
        adapter.deregister("ch")
        assert adapter.get_payloads("ch") == []
