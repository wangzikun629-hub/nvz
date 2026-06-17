import asyncio
from contextvars import ContextVar
from typing import Any, Optional


project_progress_queue_var: ContextVar[Optional[asyncio.Queue]] = ContextVar(
    "project_progress_queue_var",
    default=None,
)


def init_round_project_progress_queue():
    return project_progress_queue_var.set(asyncio.Queue())


def reset_round_project_progress_queue(token) -> None:
    project_progress_queue_var.reset(token)


def get_round_project_progress_queue() -> Optional[asyncio.Queue]:
    return project_progress_queue_var.get()


def publish_project_progress(
    text: str,
    *,
    stage: Optional[str] = None,
    status: str = "in_progress",
    detail: Any = None,
) -> None:
    queue = project_progress_queue_var.get()
    if queue is None or not text:
        return
    queue.put_nowait(
        {
            "type": "project_stage",
            "stage": stage or "generic",
            "status": status,
            "text": text,
            "detail": detail if detail is not None else "",
        }
    )


def publish_project_answer_delta(text: str) -> None:
    queue = project_progress_queue_var.get()
    if queue is None or not text:
        return
    queue.put_nowait(
        {
            "type": "project_answer_delta",
            "text": text,
        }
    )


def publish_project_answer_final(text: str) -> None:
    queue = project_progress_queue_var.get()
    if queue is None or not text:
        return
    queue.put_nowait(
        {
            "type": "project_answer_final",
            "text": text,
        }
    )


def close_project_progress() -> None:
    queue = project_progress_queue_var.get()
    if queue is None:
        return
    if not queue.empty():
        items = list(queue._queue)  # type: ignore[attr-defined]
        if any(isinstance(item, dict) and item.get("type") == "project_stage_end" for item in items):
            return
    queue.put_nowait({"type": "project_stage_end"})
