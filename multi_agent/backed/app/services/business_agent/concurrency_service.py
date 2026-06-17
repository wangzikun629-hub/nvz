from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import perf_counter
from typing import Any, Awaitable, Callable, TypeVar

from multi_agent.backed.app.infrastructure.logging.logger import logger

T = TypeVar("T")


class BusinessAgentConcurrencyService:
    def __init__(
        self,
        *,
        planner_limit: int = 2,
        answer_limit: int = 2,
        knowledge_limit: int = 4,
        analysis_workers: int = 6,
    ) -> None:
        self._planner_semaphore = asyncio.Semaphore(max(1, planner_limit))
        self._answer_semaphore = asyncio.Semaphore(max(1, answer_limit))
        self._knowledge_semaphore = asyncio.Semaphore(max(1, knowledge_limit))
        self._analysis_executor = ThreadPoolExecutor(
            max_workers=max(1, analysis_workers),
            thread_name_prefix="business-analysis",
        )
        self._analysis_lock = Lock()
        self._analysis_submitted = 0
        self._analysis_active = 0
        self._analysis_workers = max(1, analysis_workers)

    def analysis_threadpool_queue_size(self) -> int:
        with self._analysis_lock:
            return max(0, self._analysis_submitted - self._analysis_active - self._analysis_workers)

    async def run_planner_llm(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        workflow_run_id: str | None = None,
    ) -> T:
        return await self._run_limited(
            "planner",
            self._planner_semaphore,
            operation,
            workflow_run_id=workflow_run_id,
        )

    async def run_answer_llm(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        workflow_run_id: str | None = None,
    ) -> T:
        return await self._run_limited(
            "answer",
            self._answer_semaphore,
            operation,
            workflow_run_id=workflow_run_id,
        )

    async def run_knowledge(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        workflow_run_id: str | None = None,
    ) -> T:
        return await self._run_limited(
            "knowledge",
            self._knowledge_semaphore,
            operation,
            workflow_run_id=workflow_run_id,
        )

    async def run_analysis_blocking(
        self,
        operation: Callable[..., T],
        /,
        *args: Any,
        workflow_run_id: str | None = None,
        **kwargs: Any,
    ) -> T:
        submit_started_at = perf_counter()
        with self._analysis_lock:
            self._analysis_submitted += 1
            queue_size = max(0, self._analysis_submitted - self._analysis_active - self._analysis_workers)

        def invoke() -> T:
            started_at = perf_counter()
            with self._analysis_lock:
                self._analysis_active += 1
            try:
                wait_ms = (started_at - submit_started_at) * 1000
                logger.info(
                    "business_runtime concurrency=analysis workflow_run_id=%s analysis_threadpool_queue_size=%d analysis_wait_ms=%.2f",
                    workflow_run_id,
                    queue_size,
                    wait_ms,
                )
                return operation(*args, **kwargs)
            finally:
                with self._analysis_lock:
                    self._analysis_active -= 1
                    self._analysis_submitted -= 1

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._analysis_executor, invoke)

    async def _run_limited(
        self,
        name: str,
        semaphore: asyncio.Semaphore,
        operation: Callable[[], Awaitable[T]],
        *,
        workflow_run_id: str | None = None,
    ) -> T:
        wait_started_at = perf_counter()
        async with semaphore:
            wait_ms = (perf_counter() - wait_started_at) * 1000
            logger.info(
                "business_runtime concurrency=%s workflow_run_id=%s %s_wait_ms=%.2f",
                name,
                workflow_run_id,
                name,
                wait_ms,
            )
            return await operation()


business_agent_concurrency_service = BusinessAgentConcurrencyService()
