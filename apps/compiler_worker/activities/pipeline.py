"""Activity registry helpers for the compilation workflow."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from apps.compiler_worker.models import CompilationContext, CompilationStage, StageExecutionResult

StageHandler = Callable[[CompilationContext], Awaitable[StageExecutionResult]]
RollbackHandler = Callable[[CompilationContext, StageExecutionResult], Awaitable[None]]


@dataclass
class ActivityRegistry:
    """Map workflow stages to async activity and rollback handlers."""

    stage_handlers: dict[CompilationStage, StageHandler] = field(default_factory=dict)
    rollback_handlers: dict[CompilationStage, RollbackHandler] = field(default_factory=dict)

    async def run_stage(
        self,
        stage: CompilationStage,
        context: CompilationContext,
    ) -> StageExecutionResult:
        handler = self.stage_handlers.get(stage)
        if handler is None:
            raise KeyError(f"No activity handler registered for stage {stage.value}.")
        return await handler(context)

    async def rollback_stage(
        self,
        stage: CompilationStage,
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None:
        handler = self.rollback_handlers.get(stage)
        if handler is None:
            return
        await handler(context, result)
