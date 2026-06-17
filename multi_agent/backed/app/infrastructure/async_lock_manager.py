"""Per-key asyncio.Lock registry.

Usage:
    from multi_agent.backed.app.infrastructure.async_lock_manager import key_lock

    async with key_lock("session", user_id, session_id):
        state = await repo.aload(...)
        state["x"] = 1
        await repo.asave(...)
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator
from contextlib import asynccontextmanager

_registries: dict[str, dict[str, asyncio.Lock]] = {}


def _get_lock(registry: str, key: str) -> asyncio.Lock:
    bucket = _registries.setdefault(registry, {})
    if key not in bucket:
        bucket[key] = asyncio.Lock()
    return bucket[key]


@asynccontextmanager
async def key_lock(registry: str, *key_parts: str) -> AsyncIterator[None]:
    """Acquire the lock for the given registry + key parts.

    Example::

        async with key_lock("project_state", user_id, session_id):
            ...
    """
    key = "\x00".join(key_parts)
    lock = _get_lock(registry, key)
    async with lock:
        yield
