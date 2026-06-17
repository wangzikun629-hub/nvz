from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.infrastructure.tools.mcp.mcp_pool import technical_agent_pool
from multi_agent.backed.app.infrastructure.tools.mcp.mcp_servers import search_mcp_client


async def mcp_connect():
    """启动时初始化 MCP 连接池（替代原单连接 connect）。"""
    try:
        await technical_agent_pool.initialize()
        logger.info("MCP pool initialized (size=%d)", technical_agent_pool._pool_size)
    except Exception as e:
        logger.error("MCP pool init failed: %s", str(e))


async def mcp_cleanup():
    """关闭时清理 MCP 连接池。"""
    try:
        await technical_agent_pool.cleanup()
        logger.info("MCP pool cleaned up")
    except Exception as e:
        logger.warning("MCP pool cleanup failed: %s", str(e))
