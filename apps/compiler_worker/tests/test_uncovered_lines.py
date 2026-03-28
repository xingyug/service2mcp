"""Unit tests for compiler_worker uncovered lines."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from apps.compiler_worker.celery_app import _run_coro
from apps.compiler_worker.executor import DatabaseWorkflowCompilationExecutor
from apps.compiler_worker.models import CompilationRequest
from apps.compiler_worker.repository import SQLAlchemyCompilationJobStore


class TestCeleryAppUncoveredLines:
    def test_run_coro_no_running_loop_uses_asyncio_run(self) -> None:
        """Test lines 78-79: use asyncio.run when no event loop running."""
        async def test_coro():
            return "test_result"
        
        with patch('asyncio.get_running_loop', side_effect=RuntimeError("No running loop")), \
             patch('asyncio.run', return_value="test_result") as mock_run:
            
            result = _run_coro(test_coro())
            
            assert result == "test_result"
            mock_run.assert_called_once()

    def test_run_coro_with_running_loop_uses_thread_pool(self) -> None:
        """Test lines 102-104 (actually 81-83): use ThreadPoolExecutor when loop exists."""
        async def test_coro():
            return "test_result_thread"
        
        # Mock a running event loop
        mock_loop = MagicMock()
        
        with patch('asyncio.get_running_loop', return_value=mock_loop), \
             patch.object(ThreadPoolExecutor, 'submit') as mock_submit:
            
            # Mock the future returned by submit
            mock_future = MagicMock()
            mock_future.result.return_value = "test_result_thread"
            mock_submit.return_value = mock_future
            
            result = _run_coro(test_coro())
            
            assert result == "test_result_thread"
            mock_submit.assert_called_once()
            mock_future.result.assert_called_once()


class TestExecutorUncoveredLines:
    pass


class TestCompilerWorkerRepositoryUncoveredLines:
    pass
