"""Technical Agent 定义

- technical_agent_kb_only  : 仅使用本地知识库，不依赖 MCP，可安全共享为全局单例。
- technical_agent_pool     : 从 mcp_pool 导入的连接池，并发安全；每次使用通过
                             `async with technical_agent_pool.acquire() as agent` 借出。

旧代码如果直接引用 `technical_agent` 单例，仍可使用——它指向连接池中的第一个槽位
agent（仅做兼容性保留，新代码请改用 pool.acquire()）。
"""

from agents import Agent, ModelSettings, RunConfig, Runner

from multi_agent.backed.app.infrastructure.ai.openai_client import sub_model
from multi_agent.backed.app.infrastructure.ai.prompt_loader import load_prompt
from multi_agent.backed.app.infrastructure.tools.local.knowledge_base import query_knowledge
from multi_agent.backed.app.infrastructure.tools.mcp.mcp_pool import technical_agent_pool


technical_prompt = load_prompt("technical_agent")

# 仅知识库版本——无 MCP，全局单例安全
technical_agent_kb_only = Agent(
    name="诺唯赞生物科技有限公司资讯与技术专家（仅知识库）",
    instructions=(
        technical_prompt
        + "\n\n## 当前运行限制\n"
        + "当前联网搜索工具不可用。\n"
        + "1. 不要调用 `bailian_web_search`\n"
        + "2. 只能使用 `query_knowledge` 回答\n"
        + "3. 如果知识库没有结果或结果不足，直接说明当前无法联网检索最新信息\n"
    ),
    model=sub_model,
    model_settings=ModelSettings(temperature=0),
    tools=[query_knowledge],
)

# 连接池——并发安全，通过 acquire() 使用
# （此名称供外部 `from technical_agent import technical_agent_pool` 导入）
__all__ = ["technical_agent_kb_only", "technical_agent_pool"]


async def run_single_test(case_name: str, input_text: str):
    print(f"\n{'=' * 80}")
    print(f"测试用例: {case_name}")
    print(f"输入: \"{input_text}\"")
    print("-" * 80)
    try:
        async with technical_agent_pool.acquire() as agent:
            result = await Runner.run(
                agent,
                input=input_text,
                run_config=RunConfig(tracing_disabled=True),
            )
            print(f"\n\nAgent 的最终输出: {result.final_output}")
    except Exception as e:
        print(f"\nError: {e}\n")


async def main():
    await technical_agent_pool.initialize()
    try:
        test_cases = [
            ("Case 1", "诺唯赞相关技术问题示例"),
        ]
        for name, question in test_cases:
            await run_single_test(name, question)
    finally:
        await technical_agent_pool.cleanup()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
