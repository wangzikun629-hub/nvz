from agents import Agent, ModelSettings, set_tracing_disabled

from multi_agent.backed.app.infrastructure.ai.openai_client import sub_model
from multi_agent.backed.app.infrastructure.ai.prompt_loader import load_prompt


set_tracing_disabled(True)

project_business_agent = Agent(
    name="项目分析业务智能体",
    instructions=load_prompt("project_business_agent"),
    model=sub_model,
    model_settings=ModelSettings(
        temperature=0,
        max_tokens=1024,
    ),
    tools=[],
)
