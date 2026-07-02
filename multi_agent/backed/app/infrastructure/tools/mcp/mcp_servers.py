import asyncio
import json
from typing import Any, Dict

from agents.mcp import MCPServerStreamableHttp

from multi_agent.backed.app.config.settings import settings


search_mcp_client = MCPServerStreamableHttp(
    name="通用互联网搜索",
    params={
        "url": f"{settings.DASHSCOPE_BASE_URL}",
        "headers": {
            "Authorization": f"Bearer {settings.DASHSCOPE_API_KEY}"
        },
        "timeout": 60,
        "sse_read_timeout": 60 * 30,
    },
    client_session_timeout_seconds=60 * 10,
    cache_tools_list=True,
)


async def run_mcp_call(
    mcp_instance: MCPServerStreamableHttp,
    tool_name: str,
    tool_args: Dict[str, Any],
):
    server_name = mcp_instance.name
    connected = False

    print(f"\n{'=' * 60}")
    print(f"[Test Start] Service: {server_name}")
    print(f"{'=' * 60}")

    try:
        print("[Connect] Connecting to service...")
        await mcp_instance.connect()
        connected = True
        print("[Connect] Success")

        print("\n[List] Loading tool list and schemas...")
        tools_list = await mcp_instance.list_tools()

        if tools_list:
            print(f"Found {len(tools_list)} tools:")
            for i, tool in enumerate(tools_list, 1):
                print(f"\n[{i}] Tool: {tool.name}")
                print(f"Description: {tool.description}")
                print("Input schema:")
                print(json.dumps(tool.inputSchema, indent=2, ensure_ascii=False))
        else:
            print("No tools returned")

        print(f"\n{'-' * 40}")
        print(f"Args: {json.dumps(tool_args, ensure_ascii=False)}")

        result = await mcp_instance.call_tool(tool_name, tool_args)
        print("\n[Response] Service returned:")

        for content in result.content:
            if hasattr(content, "text"):
                print(content.text)
            else:
                print(f"[Non-Text]: {content}")

    except Exception as e:
        print(f"\n[Error] Test failed: {str(e)}")
        import traceback
        traceback.print_exc()

    finally:
        if connected:
            print("\n[Cleanup] Releasing connection...")
            try:
                await mcp_instance.cleanup()
            except Exception as cleanup_error:
                print(f"[Cleanup] Failed: {cleanup_error}")
            else:
                print(f"{server_name} test finished\n")


async def test_bailian_search():
    await run_mcp_call(
        mcp_instance=search_mcp_client,
        tool_name="bailian_web_search",
        tool_args={"query": "小米公司今天的股价如何"},
    )


async def main():
    await test_bailian_search()


if __name__ == "__main__":
    asyncio.run(main())
