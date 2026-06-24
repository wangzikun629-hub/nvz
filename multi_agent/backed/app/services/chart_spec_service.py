"""
chart_spec_service.py
─────────────────────
接收项目提取好的数据 + 用户自然语言需求，调用 LLM 生成 Plotly JSON spec。

主入口：
    await ChartSpecService.generate(input_data, user_request) -> dict

返回值（plotly_spec）：
    {
        "data": [...],    # Plotly trace 列表
        "layout": {...}   # Plotly layout 对象
    }

若 LLM 返回无法解析，自动回退到内置默认 spec。
"""
from __future__ import annotations

import json
import re
from typing import Any

from multi_agent.backed.app.infrastructure.ai.openai_client import (
    sub_model_client,
    AL_BAILIAN_API_KEY,
    AL_BAILIAN_BASE_URL,
    SUB_MODEL_NAME,
    main_model_client,
    SF_API_KEY,
    SF_BASE_URL,
    MAIN_MODEL_NAME,
)
from multi_agent.backed.app.infrastructure.ai.prompt_loader import load_prompt
from multi_agent.backed.app.infrastructure.logging.logger import logger

# 优先用子模型（成本低、速度快）；若未配置则降级到主模型
_USE_SUB = bool(AL_BAILIAN_API_KEY and AL_BAILIAN_BASE_URL)
_CLIENT  = sub_model_client if _USE_SUB else main_model_client
_MODEL   = SUB_MODEL_NAME   if _USE_SUB else MAIN_MODEL_NAME

# 主模型作为后备（子模型失败时）
_FALLBACK_CLIENT = main_model_client
_FALLBACK_MODEL  = MAIN_MODEL_NAME

# ── 默认 spec（LLM 完全失败时使用） ─────────────────────────────────────────

_COLORS = ["#1F5E9C", "#4F8CC9", "#D86F45", "#7C6AEB", "#2E8B57", "#B7791F"]

_BASE_LAYOUT: dict[str, Any] = {
    "plot_bgcolor":  "#FFFFFF",
    "paper_bgcolor": "#FFFFFF",
    "autosize":      True,
    "font":          {"family": "Arial, sans-serif", "color": "#334155"},
    "hoverlabel":    {"bgcolor": "#1F2937", "font": {"color": "white", "size": 12}},
    "margin":        {"t": 60, "b": 90, "l": 60, "r": 20},
}


def _default_spec(input_data: dict[str, Any]) -> dict[str, Any]:
    """当 LLM 不可用或解析失败时，返回简单的内置 Plotly spec。"""
    data_block  = input_data.get("data", {})
    labels      = data_block.get("labels", [])
    values      = data_block.get("values", [])
    ylabel      = data_block.get("ylabel", "Value")
    metric      = input_data.get("metric", "")
    project_id  = input_data.get("project_id", "")
    chart_hint  = input_data.get("chart_type_hint", "bar")
    matrix      = data_block.get("matrix")
    metric_labels = data_block.get("metric_labels")

    title = f"{project_id} {metric.upper()} Chart"
    layout = {**_BASE_LAYOUT,
              "title": {"text": title, "font": {"size": 14, "color": "#111827"}},
              "xaxis": {"title": "Sample", "tickangle": -30, "gridcolor": "#E6ECF2"},
              "yaxis": {"title": ylabel, "gridcolor": "#E6ECF2"}}

    # 热图
    if chart_hint == "heatmap" and matrix:
        return {
            "data": [{
                "type": "heatmap",
                "x": labels, "y": labels, "z": matrix,
                "colorscale": [[0, "#2F5597"], [0.25, "#8FB9DA"],
                               [0.5, "#F7F9FC"], [0.75, "#F6B26B"], [1, "#B03A2E"]],
                "zmin": -1, "zmax": 1,
                "hovertemplate": "<b>%{y}</b> vs <b>%{x}</b><br>r = %{z:.3f}<extra></extra>",
            }],
            "layout": {**layout, "yaxis": {"autorange": "reversed"}},
        }

    # 分组柱状图
    if chart_hint == "grouped_bar" and matrix and metric_labels:
        traces = []
        for i, ml in enumerate(metric_labels):
            col = [row[i] for row in matrix]
            traces.append({
                "type": "bar",
                "name": ml,
                "x": labels,
                "y": col,
                "marker": {"color": _COLORS[i % len(_COLORS)]},
                "hovertemplate": f"<b>%{{x}}</b><br>{ml}: %{{y:.2f}}<extra></extra>",
            })
        return {
            "data": traces,
            "layout": {**layout, "barmode": "group",
                        "legend": {"orientation": "h", "y": -0.22, "xanchor": "center", "x": 0.5},
                        "margin": {"t": 60, "b": 110, "l": 60, "r": 20}},
        }

    # 折线图
    if chart_hint == "line":
        return {
            "data": [{
                "type": "scatter",
                "mode": "lines+markers+text",
                "x": labels, "y": values,
                "line":   {"color": "#1F5E9C", "width": 2.5},
                "marker": {"color": "white", "size": 9,
                           "line": {"color": "#1F5E9C", "width": 2}},
                "fill":      "tozeroy",
                "fillcolor": "rgba(31,94,156,0.07)",
                "text":         [f"{v:.2f}" for v in values],
                "textposition": "top center",
                "hovertemplate": f"<b>%{{x}}</b><br>{ylabel}: %{{y:.2f}}<extra></extra>",
            }],
            "layout": layout,
        }

    # 默认：柱状图
    bar_colors = [_COLORS[i % len(_COLORS)] for i in range(len(labels))]
    return {
        "data": [{
            "type": "bar",
            "x": labels, "y": values,
            "marker": {"color": bar_colors, "line": {"color": "white", "width": 1.2}},
            "text":         [f"{v:.2f}" for v in values],
            "textposition": "outside",
            "hovertemplate": f"<b>%{{x}}</b><br>{ylabel}: %{{y:.2f}}<extra></extra>",
        }],
        "layout": {**layout,
                   "yaxis": {"title": ylabel, "gridcolor": "#E6ECF2",
                              "range": [0, max(values) * 1.14] if values else [0, 1]}},
    }


# ── JSON 提取 ────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict[str, Any] | None:
    """从 LLM 原始输出中提取第一个合法 JSON 对象。"""
    # 去掉 markdown 代码块
    text = re.sub(r"```(?:json)?", "", text).strip()

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试找到第一个 { ... }
    match = re.search(r"\{[\s\S]+\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


# ── LLM 调用 ────────────────────────────────────────────────────────────────

async def _call_llm(
    system_prompt: str,
    user_message: str,
    client,
    model: str,
) -> str | None:
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            temperature=0.2,   # 低温：JSON 格式要稳定
            max_tokens=4096,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        logger.warning("chart_spec_service LLM call failed model=%s: %s", model, exc)
        return None


# ── 公开接口 ─────────────────────────────────────────────────────────────────

class ChartSpecService:
    """将项目数据 + 用户需求转换为 Plotly JSON spec。"""

    _system_prompt: str | None = None

    @classmethod
    def _get_system_prompt(cls) -> str:
        if cls._system_prompt is None:
            cls._system_prompt = load_prompt("chart_spec")
        return cls._system_prompt

    @classmethod
    async def generate(
        cls,
        input_data: dict[str, Any],
        user_request: str,
    ) -> dict[str, Any]:
        """
        Parameters
        ----------
        input_data : dict
            {
              "metric": str,
              "chart_type_hint": str,   # "bar" / "line" / "grouped_bar" / "heatmap"
              "project_id": str,
              "data": {
                "labels": list[str],
                "values": list[float] | None,
                "ylabel": str,
                "metric_labels": list[str] | None,
                "matrix": list[list[float]] | None,
              }
            }
        user_request : str
            用户的自然语言图表需求，如 "加一条 0.1 的红色阈值线，柱子用绿色"

        Returns
        -------
        dict  —  {"data": [...], "layout": {...}}
        """
        # 组装给 LLM 的完整输入
        llm_input = {**input_data, "user_request": user_request}
        user_message = json.dumps(llm_input, ensure_ascii=False, indent=2)

        system_prompt = cls._get_system_prompt()

        # 第一次尝试：子模型
        raw = await _call_llm(system_prompt, user_message, _CLIENT, _MODEL)
        spec = _extract_json(raw) if raw else None

        # 第一次失败 → 用主模型重试
        if spec is None and _USE_SUB and SF_API_KEY and SF_BASE_URL:
            logger.info("chart_spec_service: sub_model failed, retrying with main_model")
            raw = await _call_llm(
                system_prompt, user_message, _FALLBACK_CLIENT, _FALLBACK_MODEL
            )
            spec = _extract_json(raw) if raw else None

        # 两次都失败 → 内置默认 spec
        if spec is None:
            logger.warning(
                "chart_spec_service: LLM output could not be parsed, using default spec"
            )
            return _default_spec(input_data)

        # 基本结构校验
        if not isinstance(spec.get("data"), list) or "layout" not in spec:
            logger.warning(
                "chart_spec_service: LLM spec missing data/layout keys, using default spec"
            )
            return _default_spec(input_data)

        logger.info(
            "chart_spec_service: spec generated metric=%s traces=%d",
            input_data.get("metric"),
            len(spec["data"]),
        )
        return spec


chart_spec_service = ChartSpecService()
