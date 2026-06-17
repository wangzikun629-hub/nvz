from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from multi_agent.backed.app.harness.expert_eval.evaluator import (
    build_prompt,
    evaluate_response,
    load_cases,
)


HARNESS_ROOT = Path(__file__).resolve().parent
DEFAULT_CASES = HARNESS_ROOT / "expert_eval" / "cases.jsonl"


def _chat_completion(model: dict[str, Any], messages: list[dict[str, str]]) -> str:
    api_key = str(model.get("api_key") or os.getenv(str(model.get("api_key_env") or ""), ""))
    if not api_key:
        raise RuntimeError(f"missing API key for model {model.get('name')}")
    request_payload = {
        "model": model["model"],
        "messages": messages,
        "temperature": float(model.get("temperature", 0.1)),
        "max_tokens": int(model.get("max_tokens", 1200)),
    }
    timeout = int(model.get("timeout", 120))
    try:
        from openai import OpenAI

        client = OpenAI(
            base_url=str(model.get("base_url") or ""),
            api_key=api_key,
            timeout=timeout,
        )
        response = client.chat.completions.create(**request_payload)
        return str(response.choices[0].message.content or "")
    except ImportError:
        pass

    url = str(model.get("base_url") or "").rstrip("/") + "/chat/completions"
    payload = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data["choices"][0]["message"]["content"])


def run_comparison(
    *,
    cases: list[dict[str, Any]],
    models: list[dict[str, Any]],
    response_fixture: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    model_results: list[dict[str, Any]] = []
    for model in models:
        name = str(model.get("name") or model.get("model") or "model")
        case_results: list[dict[str, Any]] = []
        for case in cases:
            case_id = str(case.get("id") or "")
            answer = (
                (response_fixture or {}).get(name, {}).get(case_id)
                if response_fixture
                else None
            )
            error = ""
            started_at = perf_counter()
            if answer is None:
                try:
                    answer = _chat_completion(model, build_prompt(case))
                except Exception as exc:
                    answer = ""
                    error = f"{type(exc).__name__}: {exc}"
            latency_ms = round((perf_counter() - started_at) * 1000, 2)
            evaluation = evaluate_response(case, answer)
            case_results.append(
                {
                    **evaluation,
                    "answer": answer,
                    "error": error,
                    "latency_ms": latency_ms,
                    "answer_chars": len(answer),
                }
            )
        model_results.append(
            {
                "name": name,
                "model": model.get("model"),
                "average_score": round(
                    sum(item["score"] for item in case_results) / max(1, len(case_results)),
                    2,
                ),
                "passed_cases": sum(1 for item in case_results if item["passed"]),
                "failed_requests": sum(1 for item in case_results if item["error"]),
                "average_latency_ms": round(
                    sum(item["latency_ms"] for item in case_results) / max(1, len(case_results)),
                    2,
                ),
                "total_cases": len(case_results),
                "cases": case_results,
            }
        )
    model_results.sort(key=lambda item: (-item["average_score"], -item["passed_cases"], item["name"]))
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "case_count": len(cases),
        "ranking": [
            {
                "rank": index,
                "name": item["name"],
                "average_score": item["average_score"],
                "passed_cases": item["passed_cases"],
                "failed_requests": item["failed_requests"],
                "average_latency_ms": item["average_latency_ms"],
                "total_cases": item["total_cases"],
            }
            for index, item in enumerate(model_results, start=1)
        ],
        "models": model_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare OpenAI-compatible models on expert bioinformatics cases.")
    parser.add_argument("--models-config", required=True, help="JSON file containing a list of model endpoint configs.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--responses", help="Optional offline response fixture JSON keyed by model name and case id.")
    parser.add_argument("--output", default=str(HARNESS_ROOT / "reports" / "model_comparison.json"))
    args = parser.parse_args()

    models = json.loads(Path(args.models_config).read_text(encoding="utf-8"))
    responses = json.loads(Path(args.responses).read_text(encoding="utf-8")) if args.responses else None
    report = run_comparison(cases=load_cases(args.cases), models=models, response_fixture=responses)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["ranking"], ensure_ascii=False, indent=2))
    print(f"report={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
