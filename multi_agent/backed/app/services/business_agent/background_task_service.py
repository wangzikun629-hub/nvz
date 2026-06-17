from __future__ import annotations

import asyncio
from dataclasses import dataclass
from itertools import count
from typing import Any, Callable

from multi_agent.backed.app.infrastructure.logging.logger import logger


@dataclass
class _QueuedTask:
    task_id: int
    task_name: str
    operation: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class BackgroundTaskService:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[_QueuedTask] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._task_ids = count(1)

    def queue_size(self) -> int:
        if self._queue is None:
            return 0
        return self._queue.qsize()

    def submit(
        self,
        task_name: str,
        operation: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> int | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation(*args, **kwargs)
            return None

        if self._queue is None:
            self._queue = asyncio.Queue()
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = loop.create_task(self._worker_loop())

        task_id = next(self._task_ids)
        self._queue.put_nowait(
            _QueuedTask(
                task_id=task_id,
                task_name=task_name,
                operation=operation,
                args=args,
                kwargs=kwargs,
            )
        )
        logger.info(
            "background_task queued task=%s task_id=%s background_task_queue_size=%d",
            task_name,
            task_id,
            self._queue.qsize(),
        )
        return task_id

    async def _worker_loop(self) -> None:
        assert self._queue is not None
        while True:
            task = await self._queue.get()
            try:
                logger.info(
                    "background_task start task=%s task_id=%s background_task_queue_size=%d",
                    task.task_name,
                    task.task_id,
                    self._queue.qsize(),
                )
                await asyncio.to_thread(task.operation, *task.args, **task.kwargs)
                logger.info(
                    "background_task done task=%s task_id=%s background_task_queue_size=%d",
                    task.task_name,
                    task.task_id,
                    self._queue.qsize(),
                )
            except Exception as exc:
                logger.warning(
                    "background_task failed task=%s task_id=%s error=%s",
                    task.task_name,
                    task.task_id,
                    str(exc),
                )
            finally:
                self._queue.task_done()


background_task_service = BackgroundTaskService()
