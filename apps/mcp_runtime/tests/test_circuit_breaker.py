"""Unit tests for apps/mcp_runtime/circuit_breaker.py."""

from __future__ import annotations

import pytest

from apps.mcp_runtime.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError


class TestCircuitBreakerOpenError:
    def test_is_runtime_error(self) -> None:
        assert issubclass(CircuitBreakerOpenError, RuntimeError)

    def test_message_preserved(self) -> None:
        err = CircuitBreakerOpenError("test message")
        assert str(err) == "test message"


class TestCircuitBreakerDefaults:
    def test_default_failure_threshold(self) -> None:
        cb = CircuitBreaker(operation_id="op1")
        assert cb.failure_threshold == 5

    def test_default_consecutive_failures_zero(self) -> None:
        cb = CircuitBreaker(operation_id="op1")
        assert cb.consecutive_failures == 0

    def test_default_is_open_false(self) -> None:
        cb = CircuitBreaker(operation_id="op1")
        assert cb.is_open is False


class TestBeforeRequest:
    def test_passes_when_closed(self) -> None:
        cb = CircuitBreaker(operation_id="op1")
        cb.before_request()  # should not raise

    def test_raises_when_open(self) -> None:
        cb = CircuitBreaker(operation_id="op1", is_open=True)
        with pytest.raises(CircuitBreakerOpenError, match="op1"):
            cb.before_request()


class TestRecordSuccess:
    def test_resets_consecutive_failures(self) -> None:
        cb = CircuitBreaker(operation_id="op1", consecutive_failures=3)
        cb.record_success()
        assert cb.consecutive_failures == 0

    def test_closes_open_breaker(self) -> None:
        cb = CircuitBreaker(operation_id="op1", is_open=True, consecutive_failures=5)
        cb.record_success()
        assert cb.is_open is False
        assert cb.consecutive_failures == 0


class TestRecordFailure:
    def test_increments_consecutive_failures(self) -> None:
        cb = CircuitBreaker(operation_id="op1")
        cb.record_failure()
        assert cb.consecutive_failures == 1

    def test_opens_at_threshold(self) -> None:
        cb = CircuitBreaker(operation_id="op1", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False
        cb.record_failure()
        assert cb.is_open is True

    def test_stays_open_above_threshold(self) -> None:
        cb = CircuitBreaker(operation_id="op1", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is True
        cb.record_failure()
        assert cb.is_open is True
        assert cb.consecutive_failures == 3

    def test_custom_threshold(self) -> None:
        cb = CircuitBreaker(operation_id="op1", failure_threshold=1)
        cb.record_failure()
        assert cb.is_open is True


class TestFullLifecycle:
    def test_open_then_success_resets(self) -> None:
        cb = CircuitBreaker(operation_id="op1", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is True
        with pytest.raises(CircuitBreakerOpenError):
            cb.before_request()
        # half-open: external caller decides to allow a probe
        cb.record_success()
        assert cb.is_open is False
        cb.before_request()  # should not raise

    def test_interleaved_success_resets_counter(self) -> None:
        cb = CircuitBreaker(operation_id="op1", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # reset
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False  # only 2 consecutive, not 3
