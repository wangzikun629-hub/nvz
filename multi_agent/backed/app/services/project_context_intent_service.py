from __future__ import annotations

import re
from typing import Any


class ProjectContextIntentService:
    SWITCH_PATTERNS = (
        re.compile(r"(换成|切换到|改成|不是这个项目.*是|看另一个项目)", re.IGNORECASE),
    )
    CLEAR_PATTERNS = (
        re.compile(r"(不要结合项目|退出项目上下文|清空项目上下文|不看项目了)", re.IGNORECASE),
    )
    BIND_PATTERNS = (
        re.compile(r"(这个项目|该项目|当前项目)", re.IGNORECASE),
        re.compile(r"(项目|批次|样本).*(看一下|看看|分析|排查|报告)", re.IGNORECASE),
    )

    def classify(self, question: str, state: dict[str, Any] | None = None) -> str:
        normalized = " ".join((question or "").split()).strip()
        if not normalized:
            return "none"
        if any(pattern.search(normalized) for pattern in self.CLEAR_PATTERNS):
            return "clear_project_context"
        if any(pattern.search(normalized) for pattern in self.SWITCH_PATTERNS):
            return "switch_project"
        if any(pattern.search(normalized) for pattern in self.BIND_PATTERNS):
            return "bind_project"

        active_project_id = str((state or {}).get("active_project_id") or "").strip()
        if active_project_id:
            return "within_project_followup"
        return "none"


project_context_intent_service = ProjectContextIntentService()
