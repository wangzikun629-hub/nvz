"""LLM 调用指数退避重试工具

对 429 / 500 / 503 等可重试错误做最多 3 次指数退避，
防止 LLM API 限流直接把错误抛给用户。

用法::

    from multi_agent.backed.app.utils.retry_util import with_llm_retry

    result = await with_llm_retry(
        lambda: Runner.run(agent, input=text, run_config=...)
    )
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from multi_agent.backed.app.infrastructure.logging.logger import logger

T = TypeVar("T")

_MAX_ATTEMPTS: int = 3
_BASE_DELAY: float = 1.0    # 首次重试等待秒数
_MAX_DELAY: float = 30.0    # 最长等待秒数

# 触发重试的 HTTP 状态码
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 503})

# 触发重试的错误文本片段（小写匹配）
_RETRYABLE_PATTERNS: tuple[str, ...] = (
    "429",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "503",
    "service unavailable",
    "overloaded",
    "capacity",
    "quota",
)


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否属于可重试类型。"""
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and status_code in _RETRYABLE_STATUS_CODES:
        return True
    error_text = str(exc).lower()
    return any(pattern in error_text for pattern in _RETRYABLE_PATTERNS)


async def with_llm_retry(coro_factory: Callable[[], Awaitable[T]]) -> T:
    """对 LLM API 调用做指数退避重试，最多 ``_MAX_ATTEMPTS`` 次。

    ``coro_factory`` 每次调用都会返回一个新的协程，以便重新发起请求。
    不可重试的错误（如 400 / 401 / 404）会立即透传，不重试。

    Args:
        coro_factory: 无参 lambda，每次调用返回待 await 的协程。

    Returns:
        协程最终成功时的返回值。

    Raises:
        最后一次尝试的异常，或首次遇到不可重试异常时直接抛出。
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt >= _MAX_ATTEMPTS - 1:
                raise
            # jitter 抖动：避免大量请求同时重试造成二次冲击
            delay = min(
                _BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5),
                _MAX_DELAY,
            )
            logger.warning(
                "LLM call failed (attempt %d/%d), retry in %.1fs | %s",
                attempt + 1,
                _MAX_ATTEMPTS,
                delay,
                str(exc)[:160],
            )
            await asyncio.sleep(delay)

    # 理论上不会走到这里，仅满足类型检查器
    assert last_exc is not None
    raise last_exc
