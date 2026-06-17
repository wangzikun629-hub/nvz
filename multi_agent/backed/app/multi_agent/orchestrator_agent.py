import asyncio
from contextlib import AsyncExitStack

from agents import Agent, ModelSettings, Runner

from multi_agent.backed.app.infrastructure.ai.openai_client import sub_model
from multi_agent.backed.app.infrastructure.ai.prompt_loader import load_prompt
from multi_agent.backed.app.infrastructure.tools.mcp.mcp_servers import search_mcp_client
from multi_agent.backed.app.multi_agent.agent_factory import AGENT_TOOLS


orchestrator_agent = Agent(
    name="主调度智能体",
    instructions=load_prompt("orchestrator_v1"),
    model=sub_model,
    model_settings=ModelSettings(temperature=0),
    tools=AGENT_TOOLS,
)


async def run_single_test(case_name: str, input_text: str) -> None:
    print(f"\n{'=' * 80}")
    print(f"测试用例: {case_name}")
    print(f'输入: "{input_text}"')
    print("-" * 80)

    async with AsyncExitStack() as stack:
        try:
            print("连接 MCP 服务中...")
            await stack.enter_async_context(search_mcp_client)
            print("处理中...")

            result = Runner.run_streamed(
                starting_agent=orchestrator_agent,
                input=input_text,
            )

            async for event in result.stream_events():
                if event.type != "run_item_stream_event":
                    continue

                if hasattr(event, "name") and event.name == "tool_called":
                    from agents import ToolCallItem

                    if isinstance(event.item, ToolCallItem):
                        raw_item = event.item.raw_item
                        print(f"\n调用工具: {raw_item.name} -> 参数: {raw_item.arguments}")
                elif hasattr(event, "name") and event.name == "tool_output":
                    from agents import ToolCallOutputItem

                    if isinstance(event.item, ToolCallOutputItem):
                        print(f"工具输出: {event.item.output}")

            print(f"\n最终输出（来自 {result.last_agent.name}）:")
            print(result.final_output)
        except Exception as exc:
            print(f"\n异常原因: {exc}\n")


async def main() -> None:
    print("\n" + "=" * 80)
    print("测试协同 Agent (Orchestrator)")
    print("=" * 80)

    test_cases = []
    for name, user_input in test_cases:
        await run_single_test(name, user_input)

    print("\n所有测试完成。\n")


if __name__ == "__main__":
    asyncio.run(main())
