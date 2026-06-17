import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from multi_agent.backed.app.schemas.request import ChatMessageRequest, UserContext
from multi_agent.backed.app.services.agent_service import MultiAgentService

PROJECT_ROOT = (
    r"D:\nvz\kefu\multi_agent\backed\app\.project_sftp_cache"
    r"\f9361f715137\VZ20260508009"
)
PROJECT_ID = "VZ20260508009"
QUESTION = "FRiP 矩阵显示了清晰的对比：IP样本自身FRiP为22.24%（PH_H3K27ac）和25.57%（PN_H3K27ac），交叉FRiP（一个IP对另一个IP的peak计算）为20.99%和21.49%，而IP样本在IgG对照上的FRiP仅1.07-1.11%，IgG自身FRiP仅3.13%。请系统解读这个FRiP矩阵：为什么两个IP样本的交叉FRiP接近自身FRiP（~21% vs 22-26%），而IP在IgG上的FRiP仅~1%？这种FRiP模式是否足以证明实验的特异性和重复性？"


async def main():
    request = ChatMessageRequest(
        query=QUESTION,
        context=UserContext(user_id="runtime_probe_user", session_id="runtime_probe_project3"),
        mode="agent",
        project_id=PROJECT_ID,
        project_root=PROJECT_ROOT,
        max_evidence_files=20,
    )
    result = await MultiAgentService.process_task_sync(request)
    path = Path(__file__).resolve().with_name("runtime_probe_project3_q2.json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    asyncio.run(main())
