from __future__ import annotations

import argparse
import asyncio
import socket
import statistics
import time
from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_AGENT_URL = "http://127.0.0.1:8000/api/chat"
DEFAULT_KNOWLEDGE_URL = "http://127.0.0.1:8001/retrieve"
DEFAULT_PROJECT_URL = "http://127.0.0.1:8000/api/project_analyze"


@dataclass
class RequestMetric:
    name: str
    ok: bool
    status_code: int
    latency_ms: float
    error: str = ""


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


async def post_json(
    client: httpx.AsyncClient,
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    name: str,
) -> RequestMetric:
    started_at = time.perf_counter()
    try:
        response = await client.post(url, json=payload, headers=headers)
        latency_ms = (time.perf_counter() - started_at) * 1000
        return RequestMetric(
            name=name,
            ok=response.is_success,
            status_code=response.status_code,
            latency_ms=latency_ms,
            error="" if response.is_success else response.text[:300],
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - started_at) * 1000
        return RequestMetric(
            name=name,
            ok=False,
            status_code=0,
            latency_ms=latency_ms,
            error=str(exc),
        )


async def run_agent_case(
    client: httpx.AsyncClient,
    *,
    url: str,
    question: str,
    index: int,
    mode: str,
    api_key: str,
    project_id: str,
    project_root: str,
    max_evidence_files: int,
) -> RequestMetric:
    payload = {
        "query": question,
        "user_id": f"load_user_{index % 8}",
        "session_id": f"load_session_{index}",
        "mode": mode,
        "flag": True,
        "max_evidence_files": max_evidence_files,
        "project_id": project_id or None,
        "project_root": project_root or None,
    }
    headers = {"x-api-key": api_key} if api_key else {}
    return await post_json(
        client,
        url=url,
        payload=payload,
        headers=headers,
        name="agent",
    )


async def run_knowledge_case(
    client: httpx.AsyncClient,
    *,
    url: str,
    question: str,
    kb_scope: str,
) -> RequestMetric:
    payload = {
        "question": question,
        "kb_scope": kb_scope or None,
    }
    return await post_json(
        client,
        url=url,
        payload=payload,
        headers={},
        name="knowledge",
    )


async def run_project_case(
    client: httpx.AsyncClient,
    *,
    url: str,
    question: str,
    index: int,
    api_key: str,
    project_id: str,
    project_root: str,
    max_evidence_files: int,
) -> RequestMetric:
    payload = {
        "question": question,
        "user_id": f"project_load_user_{index % 8}",
        "session_id": f"project_load_session_{index}",
        "project_id": project_id or None,
        "project_root": project_root or None,
        "max_evidence_files": max_evidence_files,
    }
    headers = {"x-api-key": api_key} if api_key else {}
    return await post_json(
        client,
        url=url,
        payload=payload,
        headers=headers,
        name="project",
    )


async def worker(
    semaphore: asyncio.Semaphore,
    tasks: list[tuple[str, int, str]],
    client: httpx.AsyncClient,
    *,
    agent_url: str,
    knowledge_url: str,
    project_url: str,
    agent_mode: str,
    api_key: str,
    kb_scope: str,
    agent_project_id: str,
    agent_project_root: str,
    agent_max_evidence_files: int,
    project_id: str,
    project_root: str,
    project_max_evidence_files: int,
    metrics: list[RequestMetric],
) -> None:
    while tasks:
        async with semaphore:
            try:
                target, index, question = tasks.pop()
            except IndexError:
                return
            if target == "agent":
                metric = await run_agent_case(
                    client,
                    url=agent_url,
                    question=question,
                    index=index,
                    mode=agent_mode,
                    api_key=api_key,
                    project_id=agent_project_id,
                    project_root=agent_project_root,
                    max_evidence_files=agent_max_evidence_files,
                )
            elif target == "project":
                metric = await run_project_case(
                    client,
                    url=project_url,
                    question=question,
                    index=index,
                    api_key=api_key,
                    project_id=project_id,
                    project_root=project_root,
                    max_evidence_files=project_max_evidence_files,
                )
            else:
                metric = await run_knowledge_case(
                    client,
                    url=knowledge_url,
                    question=question,
                    kb_scope=kb_scope,
                )
            metrics.append(metric)


def build_task_plan(
    *,
    target: str,
    total_requests: int,
    agent_question: str,
    knowledge_question: str,
    project_question: str,
) -> list[tuple[str, int, str]]:
    tasks: list[tuple[str, int, str]] = []
    for index in range(total_requests):
        if target == "agent":
            tasks.append(("agent", index, agent_question))
        elif target == "knowledge":
            tasks.append(("knowledge", index, knowledge_question))
        elif target == "project":
            tasks.append(("project", index, project_question))
        elif target == "all":
            current_target = ("agent", "knowledge", "project")[index % 3]
            current_question = {
                "agent": agent_question,
                "knowledge": knowledge_question,
                "project": project_question,
            }[current_target]
            tasks.append((current_target, index, current_question))
        else:
            current_target = "agent" if index % 2 == 0 else "knowledge"
            current_question = agent_question if current_target == "agent" else knowledge_question
            tasks.append((current_target, index, current_question))
    return tasks


def summarize(metrics: list[RequestMetric]) -> str:
    if not metrics:
        return "No requests executed."
    latencies = [item.latency_ms for item in metrics]
    ok_count = sum(1 for item in metrics if item.ok)
    error_count = len(metrics) - ok_count
    status_buckets: dict[str, int] = {}
    for item in metrics:
        key = str(item.status_code)
        status_buckets[key] = status_buckets.get(key, 0) + 1
    lines = [
        f"total={len(metrics)} ok={ok_count} error={error_count} error_rate={error_count / len(metrics):.2%}",
        f"latency_ms avg={statistics.mean(latencies):.2f} p50={percentile(latencies, 0.50):.2f} p95={percentile(latencies, 0.95):.2f} p99={percentile(latencies, 0.99):.2f} max={max(latencies):.2f}",
        "status_codes=" + ", ".join(f"{code}:{count}" for code, count in sorted(status_buckets.items())),
    ]
    failed = [item for item in metrics if not item.ok][:5]
    for item in failed:
        lines.append(
            f"failure target={item.name} status={item.status_code} latency_ms={item.latency_ms:.2f} error={item.error}"
        )
    return "\n".join(lines)


def summarize_by_target(metrics: list[RequestMetric]) -> str:
    grouped: dict[str, list[RequestMetric]] = {}
    for item in metrics:
        grouped.setdefault(item.name, []).append(item)
    lines: list[str] = []
    for target_name in sorted(grouped):
        lines.append(f"[{target_name}]")
        lines.append(summarize(grouped[target_name]))
    return "\n".join(lines)


def check_tcp_endpoint(url: str, timeout_seconds: float) -> tuple[bool, str]:
    parsed = httpx.URL(url)
    host = parsed.host or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True, f"{host}:{port} reachable"
    except OSError as exc:
        return False, f"{host}:{port} unreachable: {exc}"


async def main_async(args: argparse.Namespace) -> int:
    precheck_targets: list[tuple[str, str]] = []
    if args.target in {"agent", "both", "all"}:
        precheck_targets.append(("agent", args.agent_url))
    if args.target in {"knowledge", "both", "all"}:
        precheck_targets.append(("knowledge", args.knowledge_url))
    if args.target in {"project", "all"}:
        precheck_targets.append(("project", args.project_url))
    precheck_failed = False
    for target_name, target_url in precheck_targets:
        ok, message = check_tcp_endpoint(target_url, args.connect_timeout)
        print(f"precheck target={target_name} {message}")
        if not ok:
            precheck_failed = True
    if precheck_failed:
        return 2

    tasks = build_task_plan(
        target=args.target,
        total_requests=args.requests,
        agent_question=args.agent_question,
        knowledge_question=args.knowledge_question,
        project_question=args.project_question,
    )
    metrics: list[RequestMetric] = []
    timeout = httpx.Timeout(connect=args.connect_timeout, read=args.read_timeout, write=args.read_timeout, pool=5.0)
    limits = httpx.Limits(max_connections=max(args.concurrency * 2, 10), max_keepalive_connections=max(args.concurrency, 5))
    started_at = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        semaphore = asyncio.Semaphore(args.concurrency)
        workers = [
            asyncio.create_task(
                worker(
                    semaphore,
                    tasks,
                    client,
                    agent_url=args.agent_url,
                    knowledge_url=args.knowledge_url,
                    project_url=args.project_url,
                    agent_mode=args.agent_mode,
                    api_key=args.api_key,
                    kb_scope=args.kb_scope,
                    agent_project_id=args.agent_project_id,
                    agent_project_root=args.agent_project_root,
                    agent_max_evidence_files=args.agent_max_evidence_files,
                    project_id=args.project_id,
                    project_root=args.project_root,
                    project_max_evidence_files=args.project_max_evidence_files,
                    metrics=metrics,
                )
            )
            for _ in range(args.concurrency)
        ]
        await asyncio.gather(*workers)
    total_ms = (time.perf_counter() - started_at) * 1000
    print(f"target={args.target} concurrency={args.concurrency} requests={args.requests} wall_ms={total_ms:.2f}")
    print(summarize(metrics))
    print(summarize_by_target(metrics))
    return 0 if all(item.ok for item in metrics) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal load test for agent, knowledge, and project-analysis backends.")
    parser.add_argument("--target", choices=("agent", "knowledge", "project", "both", "all"), default="both")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--requests", type=int, default=40)
    parser.add_argument("--agent-url", default=DEFAULT_AGENT_URL)
    parser.add_argument("--knowledge-url", default=DEFAULT_KNOWLEDGE_URL)
    parser.add_argument("--project-url", default=DEFAULT_PROJECT_URL)
    parser.add_argument("--agent-mode", default="auto")
    parser.add_argument("--agent-project-id", default="")
    parser.add_argument("--agent-project-root", default="")
    parser.add_argument("--agent-max-evidence-files", type=int, default=10)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--kb-scope", default="")
    parser.add_argument("--agent-question", default="Please briefly describe your capabilities.")
    parser.add_argument("--knowledge-question", default="What is the FRiP metric?")
    parser.add_argument("--project-question", default="Analyze this project and summarize the main quality risks.")
    parser.add_argument("--project-id", default="")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--project-max-evidence-files", type=int, default=10)
    parser.add_argument("--connect-timeout", type=float, default=5.0)
    parser.add_argument("--read-timeout", type=float, default=120.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
