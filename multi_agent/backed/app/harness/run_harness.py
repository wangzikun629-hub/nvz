from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.harness.evaluators.assertions import (
    evaluate_business_agent,
    evaluate_project_analysis,
    evaluate_question_router,
)
from multi_agent.backed.app.harness.runners.business_agent_runner import BusinessAgentHarnessRunner
from multi_agent.backed.app.harness.runners.project_analysis_runner import ProjectAnalysisHarnessRunner
from multi_agent.backed.app.harness.runners.question_router_runner import QuestionRouterHarnessRunner
from multi_agent.backed.app.repositories.project_memory_repository import project_memory_repository
from multi_agent.backed.app.repositories.project_report_cache_repository import project_report_cache_repository
from multi_agent.backed.app.repositories.project_state_repository import project_state_repository
from multi_agent.backed.app.repositories.session_repository import session_repository
from multi_agent.backed.app.services.business_agent.experience_service import experience_service

KNOWN_FAILURES_PATH = Path(__file__).resolve().parent / "known_failures.json"


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
    # 2026-07-02 修复：project_report_cache_repository 和上面三个仓库一样，靠一个可
    # 覆写的 `_storage_root` 属性存 JSON 文件（app/project_report_caches/），此前一直
    # 没有被这里重定向，harness 跑 report_summary 相关用例时会直接读写生产目录下的
    # 真实缓存文件——线上某次生成的报告缓存混进了这次回归测试的判定里（对应
    # business_report_summary_e2e 用例一度命中一份结构过期的旧缓存，见
    # docs/project_analysis_agent_upgrade_plan.md 第四轮记录），改完这里以后 harness
    # 每次都是全新缓存目录，不会再读到跑 harness 之外产生的缓存条目，也不会污染
    # 生产缓存目录。
    project_report_cache_root = harness_runtime / "project_report_caches"
    for path in (project_memory_root, project_state_root, session_root, workspace_root, project_report_cache_root):
        path.mkdir(parents=True, exist_ok=True)

    project_memory_repository._storage_root = project_memory_root
    project_state_repository._storage_root = project_state_root
    session_repository._storage_root = session_root
    project_report_cache_repository._storage_root = project_report_cache_root
    experience_service._global_experience_path = project_memory_root / "_global_experience.json"
    os.environ["PROJECT_WORKSPACE_DIR"] = str(workspace_root)


def _coerce_like(reference: Any, raw: Any) -> Any:
    if isinstance(reference, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(raw)
    if isinstance(reference, float):
        return float(raw)
    return raw


def _apply_case_env_overrides(case: dict[str, Any]):
    """project_analysis_phase1.5_auto_promotion_revision.md §11 解决方法 2：部分用例的期望
    值绑定在某个 settings 开关的特定状态上（如 `business_report_summary_e2e` 期望
    `HARNESS_GUARD_ENFORCEMENT_ENABLED=true`），不应该依赖进程级默认值——默认值变化时，
    这条用例的期望和实际状态会各判各的，看似通过/失败其实跟这条用例本身无关。case json
    里的 `env_overrides` 显式声明本用例运行时需要的 settings 状态，运行结束后立即还原，
    不影响同一进程里跑的其它用例。返回一个 restore 回调，调用方必须在 finally 里调用。
    """
    overrides = case.get("env_overrides") or {}
    previous: dict[str, Any] = {}
    for key, raw_value in overrides.items():
        if not hasattr(settings, key):
            continue
        previous[key] = getattr(settings, key)
        setattr(settings, key, _coerce_like(previous[key], raw_value))

    def _restore() -> None:
        for key, value in previous.items():
            setattr(settings, key, value)

    return _restore


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    required_binary = str(case.get("requires_binary") or "").strip()
    if required_binary and shutil.which(required_binary) is None:
        return {
            "id": case.get("id"),
            "target": case.get("target", "project_analysis"),
            "passed": True,
            "skipped": True,
            "checks": [
                {
                    "name": "environment_prerequisite",
                    "passed": True,
                    "detail": (
                        f"skipped: required binary '{required_binary}' not found on this host; "
                        "this is an environment gap, not a code regression (see "
                        "project_analysis_phase1.5_auto_promotion_revision.md §11)."
                    ),
                }
            ],
            "case_file": case.get("_case_file"),
        }

    restore_env = _apply_case_env_overrides(case)
    try:
        return _run_case_inner(case)
    finally:
        restore_env()


def _run_case_inner(case: dict[str, Any]) -> dict[str, Any]:
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
    parser.add_argument(
        "--fail-on-new-failure-only",
        action="store_true",
        help=(
            "Gate exit code on NEW failures only (case ids not present in known_failures.json). "
            "Failures already snapshotted as known-issues are reported but do not fail the run. "
            "See project_analysis_phase1.5_auto_promotion_revision.md §11 解决方法 4 (过渡期方案)："
            "把'掩盖'变成'显式登记 + 变更告警'，直到 §11 解决方法 1（模型调用 mock/回放）落地为止。"
        ),
    )
    parser.add_argument(
        "--update-known-failures",
        action="store_true",
        help="Write the current failure set to known_failures.json (review the diff before committing).",
    )
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

    skipped = sum(1 for item in results if item.get("skipped"))
    passed = sum(1 for item in results if item["passed"])
    failed = len(results) - passed
    print(
        f"[harness] suite={args.suite} passed={passed} failed={failed} "
        f"skipped_env_prereq={skipped} total={len(results)}"
    )

    failing_ids = {result.get("id") for result in results if not result["passed"]}
    known_failures = _load_known_failures()
    known_for_suite = set(known_failures.get(args.suite, []))
    new_failures = failing_ids - known_for_suite
    stale_known = known_for_suite - failing_ids  # previously-known failures that now pass — worth pruning

    for result in results:
        if result["passed"]:
            continue
        tag = "KNOWN" if result.get("id") in known_for_suite else "NEW"
        print(f"[harness] FAILED[{tag}] {result.get('id')}")
        for check in result.get("checks", []):
            if not check.get("passed"):
                print(f"  - {check.get('name')}: {check.get('detail')}")

    if known_for_suite:
        print(f"[harness] known_failures (snapshotted, not gating)={sorted(known_for_suite & failing_ids)}")
    if stale_known:
        print(
            f"[harness] known_failures now passing (safe to remove from known_failures.json)="
            f"{sorted(stale_known)}"
        )

    if not args.no_report:
        report_path = _write_report(args.suite, results)
        print(f"[harness] report={report_path}")

    if args.update_known_failures:
        known_failures[args.suite] = sorted(failing_ids)
        _write_known_failures(known_failures)
        print(f"[harness] known_failures.json updated for suite={args.suite}: {sorted(failing_ids)}")

    if args.fail_on_new_failure_only:
        if new_failures:
            print(f"[harness] NEW failures not in known_failures.json: {sorted(new_failures)}")
        return 0 if not new_failures else 1

    return 0 if failed == 0 else 1


def _load_known_failures() -> dict[str, list[str]]:
    if not KNOWN_FAILURES_PATH.exists():
        return {}
    try:
        payload = json.loads(KNOWN_FAILURES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_known_failures(payload: dict[str, list[str]]) -> None:
    KNOWN_FAILURES_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
