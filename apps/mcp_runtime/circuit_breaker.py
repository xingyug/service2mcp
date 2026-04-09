"""Simple per-operation circuit breaker for upstream proxying."""

from __future__ import annotations

from dataclasses import dataclass


class CircuitBreakerOpenError(RuntimeError):
    """Raised when an operation's circuit breaker is open."""


@dataclass
class CircuitBreaker:
    """Counts consecutive failures and opens after a configured threshold."""

    operation_id: str
    failure_threshold: int = 5
    consecutive_failures: int = 0
    is_open: bool = False

    def before_request(self) -> None:
        if self.is_open:
            raise CircuitBreakerOpenError(
                f"Circuit breaker is open for operation {self.operation_id}."
            )

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.is_open = False

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.is_open = True
