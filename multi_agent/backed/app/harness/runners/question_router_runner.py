from __future__ import annotations

from time import perf_counter
from typing import Any

from multi_agent.backed.app.services.question_router_service import question_router_service


class QuestionRouterHarnessRunner:
    def run(self, case: dict[str, Any]) -> dict[str, Any]:
        started_at = perf_counter()
        active_project_id = case.get("active_project_id")
        examples = []
        for example in case.get("examples", []) or []:
            route = question_router_service.classify(
                str(example.get("question") or ""),
                active_project_id=example.get("active_project_id", active_project_id),
            )
            examples.append(
                {
                    "id": example.get("id"),
                    "question": example.get("question"),
                    "route": route.to_dict(),
                    "expect": example.get("expect") or {},
                }
            )
        return {
            "examples": examples,
            "_harness": {
                "case_id": case.get("id"),
                "target": case.get("target", "question_router"),
                "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            },
        }
