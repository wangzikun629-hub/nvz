"""MCP 客户端连接池

每个槽位持有一个独立的 MCPServerStreamableHttp + 对应的 technical_agent 实例，
通过 asyncio.Queue 实现 acquire/release，消除多并发请求共享同一 MCP 连接的竞争问题。

用法：
    async with technical_agent_pool.acquire() as agent:
        result = await Runner.run(agent, ...)

生命周期由 mcp_manager 在 FastAPI lifespan 中统一管理。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from agents import Agent, ModelSettings
from agents.mcp import MCPServerStreamableHttp

from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.ai.openai_client import sub_model
from multi_agent.backed.app.infrastructure.ai.prompt_loader import load_prompt
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.infrastructure.tools.local.knowledge_base import query_knowledge


def _make_mcp_client() -> MCPServerStreamableHttp:
    """创建一个新的独立 MCP 客户端实例。"""
    return MCPServerStreamableHttp(
        name="通用互联网搜索",
        params={
            "url": f"{settings.DASHSCOPE_BASE_URL}",
            "headers": {
                "Authorization": f"Bearer {settings.AL_BAILIAN_API_KEY}"
            },
            "timeout": 60,
            "sse_read_timeout": 60 * 30,
        },
        client_session_timeout_seconds=60 * 10,
        cache_tools_list=True,
    )


def _make_technical_agent(mcp_client: MCPServerStreamableHttp) -> Agent:
    """基于指定 MCP 客户端创建 technical_agent 实例。"""
    technical_prompt = load_prompt("technical_agent")
    return Agent(
        name="诺唯赞生物科技有限公司资讯与技术专家",
        instructions=technical_prompt,
        model=sub_model,
        model_settings=ModelSettings(temperature=0),
        tools=[query_knowledge],
        mcp_servers=[mcp_client],
    )


class TechnicalAgentPool:
    """Technical Agent + MCP 客户端连接池。

    每个槽位 = (MCPServerStreamableHttp, Agent)，彼此独立，互不干扰。
    通过 asyncio.Queue 实现先进先出调度，天然防止超发。
    """

    def __init__(self, pool_size: int = 3):
        self._pool_size = pool_size
        self._queue: asyncio.Queue[tuple[MCPServerStreamableHttp, Agent]] | None = None
        self._slots: list[tuple[MCPServerStreamableHttp, Agent]] = []

    async def initialize(self) -> None:
        """在 FastAPI lifespan 启动时调用，建立所有连接。"""
        self._queue = asyncio.Queue()
        for i in range(self._pool_size):
            client = _make_mcp_client()
            agent = _make_technical_agent(client)
            try:
                await client.connect()
                logger.info("MCP pool slot %d/%d connected", i + 1, self._pool_size)
            except Exception as exc:
                logger.error("MCP pool slot %d connect failed: %s", i + 1, exc)
                # 连接失败的槽位仍入队，使用时会触发 fallback
            self._slots.append((client, agent))
            self._queue.put_nowait((client, agent))

    async def cleanup(self) -> None:
        """在 FastAPI lifespan 关闭时调用，清理所有连接。"""
        for client, _ in self._slots:
            try:
                await client.cleanup()
            except Exception as exc:
                logger.warning("MCP pool cleanup error: %s", exc)
        self._slots.clear()
        self._queue = None

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Agent]:
        """从池中借出一个 agent，使用完毕自动归还。"""
        if self._queue is None:
            raise RuntimeError("TechnicalAgentPool not initialized")
        client, agent = await self._queue.get()
        try:
            yield agent
        finally:
            self._queue.put_nowait((client, agent))

    @property
    def is_ready(self) -> bool:
        return self._queue is not None and not self._queue.empty()


# 全局单例，由 mcp_manager 负责生命周期
# pool_size 通过 settings.MCP_POOL_SIZE 控制（默认 15，.env 可覆盖）
technical_agent_pool = TechnicalAgentPool(pool_size=settings.MCP_POOL_SIZE)
