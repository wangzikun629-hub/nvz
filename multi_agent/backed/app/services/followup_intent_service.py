from __future__ import annotations

import re
from typing import Any


class FollowupIntentService:
    CONFIRM_PATTERNS = (
        re.compile(r"^(可以|可以的|好的|好|继续|继续吧|继续排查|那就继续|按这个继续|继续进行下一步的排查)$", re.IGNORECASE),
    )

    def classify(self, question: str, state: dict[str, Any] | None = None) -> str:
        normalized = " ".join((question or "").split()).strip()
        if not normalized:
            return "none"

        current_state = state or {}
        if not current_state.get("project_context_locked"):
            return "none"
        if not current_state.get("pending_followup_action"):
            return "none"

        if any(pattern.fullmatch(normalized) for pattern in self.CONFIRM_PATTERNS):
            return "confirm_followup_action"
        return "none"


followup_intent_service = FollowupIntentService()
