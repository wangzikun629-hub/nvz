from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from multi_agent.backed.app.harness.evaluators.assertions import (
    evaluate_business_agent,
    evaluate_project_analysis,
    evaluate_question_router,
)
from multi_agent.backed.app.harness.runners.business_agent_runner import BusinessAgentHarnessRunner
from multi_agent.backed.app.harness.runners.project_analysis_runner import ProjectAnalysisHarnessRunner
from multi_agent.backed.app.harness.runners.question_router_runner import QuestionRouterHarnessRunner
from multi_agent.backed.app.repositories.project_memory_repository import project_memory_repository
from multi_agent.backed.app.repositories.project_state_repository import project_state_repository
from multi_agent.backed.app.repositories.session_repository import session_repository
from multi_agent.backed.app.services.business_agent.experience_service import experience_service


APP_ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = Path(__file__).resolve().parent


def _load_cases(suite: str, case_id: str | None = None) -> list[dict[str, Any]]:
    case_dir = HARNESS_ROOT / "cases" / suite
    if not case_dir.exists():
        raise FileNotFoundError(f"Harness suite not found: {case_dir}")

    cases: list[dict[str, Any]] = []
    for path in sorted(case_dir.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        case["_case_file"] = str(path)
        if case.get("enabled", True) is False:
            continue
        if case_id and case.get("id") != case_id:
            continue
        cases.append(case)
    return cases


def _apply_project_root_overrides(cases: list[dict[str, Any]], values: list[str]) -> None:
    if not values:
        return

    project_ids = {str(case["project_id"]) for case in cases if case.get("project_id")}
    overrides: dict[str, str] = {}
    for value in values:
        if "=" in value:
            project_id, path = value.split("=", 1)
            project_id = project_id.strip()
            path = path.strip()
        elif len(project_ids) == 1:
            project_id = next(iter(project_ids))
            path = value.strip()
        else:
            raise ValueError("--project-root must use PROJECT_ID=PATH when the suite contains multiple projects")
        if not project_id or not path:
            raise ValueError(f"Invalid --project-root value: {value!r}")
        overrides[project_id] = path

    for case in cases:
        project_id = str(case.get("project_id") or "")
        if project_id in overrides:
            case["project_root"] = overrides[project_id]


def _configure_project_sources(base_dirs: list[str], offline: bool, runtime_dir: str | None) -> None:
    if base_dirs:
        existing = os.getenv("PROJECT_BASE_DIRS", "").strip()
        combined = [*base_dirs, *([existing] if existing else [])]
        os.environ["PROJECT_BASE_DIRS"] = ";".join(combined)
    if offline:
        os.environ["PROJECT_SFTP_OFFLINE"] = "1"

    configured_runtime = runtime_dir or os.getenv("HARNESS_RUNTIME_DIR", "").strip()
    harness_runtime = Path(configured_runtime).resolve() if configured_runtime else HARNESS_ROOT / ".runtime"
    project_memory_root = harness_runtime / "project_memories"
    project_state_root = harness_runtime / "project_session_states"
    session_root = harness_runtime / "user_memories"
    workspace_root = harness_runtime / "workspaces"
    for path in (project_memory_root, project_state_root, session_root, workspace_root):
        path.mkdir(parents=True, exist_ok=True)

    project_memory_repository._storage_root = project_memory_root
    project_state_repository._storage_root = project_state_root
    session_repository._storage_root = session_root
    experience_service._global_experience_path = project_memory_root / "_global_experience.json"
    os.environ["PROJECT_WORKSPACE_DIR"] = str(workspace_root)


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    target = case.get("target", "project_analysis")
    if target == "project_analysis":
        result = ProjectAnalysisHarnessRunner().run(case)
        checks = evaluate_project_analysis(result, case.get("expect") or {})
    elif target == "business_agent":
        result = BusinessAgentHarnessRunner().run(case)
        checks = evaluate_business_agent(result, case.get("expect") or {})
    elif target == "question_router":
        result = QuestionRouterHarnessRunner().run(case)
        checks = evaluate_question_router(result, case.get("expect") or {})
    else:
        raise ValueError(f"Unsupported harness target: {target}")

    passed = all(check.passed for check in checks)
    payload = result.get("result_payload") or {}
    answer = ""
    if isinstance(payload, dict):
        answer = str(payload.get("answer") or "")
    answer = answer or str(result.get("answer") or result.get("report") or "")
    analysis_result = result.get("data") or {}
    return {
        "id": case.get("id"),
        "target": target,
        "passed": passed,
        "checks": [check.to_dict() for check in checks],
        "run_id": result.get("run_id"),
        "trace": result.get("trace"),
        "harness": result.get("_harness"),
        "harness_guard": result.get("harness_guard")
        or (payload.get("harness_guard") if isinstance(payload, dict) else None),
        "answer_quality": result.get("answer_quality")
        or (payload.get("answer_quality") if isinstance(payload, dict) else None),
        "target_metrics": (
            (analysis_result.get("analysis_plan") or {}).get("target_metrics", [])
            if isinstance(analysis_result.get("analysis_plan"), dict)
            else []
        ),
        "question": analysis_result.get("question"),
        "target_evidence": [
            {
                "metric_key": item.get("metric_key"),
                "sample": item.get("sample"),
                "display_value": item.get("display_value"),
            }
            for item in (analysis_result.get("evidence_chain") or [])
            if isinstance(item, dict)
            and item.get("metric_key") in {"adapter_percent", "mt_rate_percent"}
        ][:12],
        "answer_excerpt": answer[:600],
        "case_file": case.get("_case_file"),
    }


def _write_report(suite: str, results: list[dict[str, Any]]) -> Path:
    report_dir = HARNESS_ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"{suite}_{stamp}.json"
    payload = {
        "suite": suite,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "failed": sum(1 for item in results if not item["passed"]),
        "results": results,
    }
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AI workflow harness cases.")
    parser.add_argument("--suite", default="project_analysis", help="Harness suite name.")
    parser.add_argument("--case", dest="case_id", default=None, help="Run one case id.")
    parser.add_argument(
        "--project-root",
        action="append",
        default=[],
        metavar="PROJECT_ID=PATH",
        help="Override a project's local root. A bare PATH is accepted when the selected cases use one project.",
    )
    parser.add_argument(
        "--project-base-dir",
        action="append",
        default=[],
        metavar="PATH",
        help="Prepend a local project base directory. May be specified more than once.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use local projects and SFTP caches only; never connect to SFTP.",
    )
    parser.add_argument(
        "--runtime-dir",
        default=None,
        metavar="PATH",
        help="Directory for isolated harness state, memories, and workflow workspaces.",
    )
    parser.add_argument("--no-report", action="store_true", help="Do not write JSON report.")
    args = parser.parse_args(argv)

    _configure_project_sources(args.project_base_dir, args.offline, args.runtime_dir)
    cases = _load_cases(args.suite, args.case_id)
    _apply_project_root_overrides(cases, args.project_root)
    if not cases:
        print(f"No harness cases found for suite={args.suite!r} case={args.case_id!r}")
        return 1

    results: list[dict[str, Any]] = []
    for case in cases:
        print(f"[harness] running {case.get('id')} target={case.get('target', 'project_analysis')}")
        try:
            results.append(_run_case(case))
        except Exception as exc:
            results.append(
                {
                    "id": case.get("id"),
                    "target": case.get("target"),
                    "passed": False,
                    "checks": [{"name": "case_execution", "passed": False, "detail": str(exc)}],
                    "case_file": case.get("_case_file"),
                }
            )

    passed = sum(1 for item in results if item["passed"])
    failed = len(results) - passed
    print(f"[harness] suite={args.suite} passed={passed} failed={failed} total={len(results)}")

    for result in results:
        if result["passed"]:
            continue
        print(f"[harness] FAILED {result.get('id')}")
        for check in result.get("checks", []):
            if not check.get("passed"):
                print(f"  - {check.get('name')}: {check.get('detail')}")

    if not args.no_report:
        report_path = _write_report(args.suite, results)
        print(f"[harness] report={report_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
