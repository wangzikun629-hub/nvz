from __future__ import annotations

from typing import Any

__all__ = [
    "BusinessAnswerQualityService",
    "BusinessAgentRuntimeService",
    "business_answer_quality_service",
    "business_agent_runtime_service",
]


def __getattr__(name: str) -> Any:
    """Keep public imports stable without eagerly importing the whole runtime."""

    if name in {"BusinessAgentRuntimeService", "business_agent_runtime_service"}:
        from multi_agent.backed.app.services.business_agent.runtime_service import (
            BusinessAgentRuntimeService,
            business_agent_runtime_service,
        )

        return {
            "BusinessAgentRuntimeService": BusinessAgentRuntimeService,
            "business_agent_runtime_service": business_agent_runtime_service,
        }[name]
    if name in {"BusinessAnswerQualityService", "business_answer_quality_service"}:
        from multi_agent.backed.app.services.business_agent.answer_quality_service import (
            BusinessAnswerQualityService,
            business_answer_quality_service,
        )

        return {
            "BusinessAnswerQualityService": BusinessAnswerQualityService,
            "business_answer_quality_service": business_answer_quality_service,
        }[name]
    raise AttributeError(name)
