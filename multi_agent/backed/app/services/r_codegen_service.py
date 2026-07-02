"""
r_codegen_service.py
────────────────────
LLM → R 脚本 → PNG 图片生成服务

流程：
  1. 将项目 QC 数据组装成 R dataframe 代码片段
  2. LLM（子模型 / 主模型）生成完整 ggplot2 R 脚本
  3. subprocess 执行 Rscript，超时 30 秒
  4. 执行失败时把 stderr 反馈给 LLM 自动修复，最多重试 1 次
  5. 成功后返回 {chart_id, image_url, r_script}
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from multi_agent.backed.app.infrastructure.ai.openai_client import (
    AL_BAILIAN_API_KEY, AL_BAILIAN_BASE_URL, SUB_MODEL_NAME,
    SF_API_KEY, SF_BASE_URL, MAIN_MODEL_NAME,
    sub_model_client, main_model_client,
)
from multi_agent.backed.app.infrastructure.ai.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

# ── 优先使用子模型（成本低）；不可用时用主模型
_USE_SUB    = bool(AL_BAILIAN_API_KEY and AL_BAILIAN_BASE_URL and SUB_MODEL_NAME)
_LLM_CLIENT = sub_model_client  if _USE_SUB else main_model_client
_LLM_MODEL  = SUB_MODEL_NAME    if _USE_SUB else MAIN_MODEL_NAME
_FB_CLIENT  = main_model_client
_FB_MODEL   = MAIN_MODEL_NAME

# PNG 存放目录（与 project_chart_service 保持一致，由 FastAPI StaticFiles 挂载）
_CHART_DIR = Path(__file__).resolve().parents[1] / "generated" / "charts"

# Rscript 执行超时（秒）
_R_TIMEOUT = 30


class RCodegenService:
    """LLM 生成 R 脚本 → 执行 → 返回 PNG 图片 URL。"""

    _codegen_prompt: str | None = None
    _fix_prompt: str | None = None

    @classmethod
    def _get_codegen_prompt(cls) -> str:
        if cls._codegen_prompt is None:
            cls._codegen_prompt = load_prompt("r_codegen")
        return cls._codegen_prompt

    # ── 构建 R dataframe 代码 ─────────────────────────────────────────────────

    @staticmethod
    def _build_r_dataframe(
        labels: list[str],
        values: list[float] | None,
        ylabel: str,
        groups: list[str] | None = None,
        *,
        x_values: list[float] | None = None,
        y_values: list[float] | None = None,
        xlabel: str = "",
        metric_labels: list[str] | None = None,
        matrix: list[list[float]] | None = None,
    ) -> str:
        """将各种数据形式转换为 R dataframe 定义代码。"""

        def _r_str_vec(lst: list[str]) -> str:
            escaped = [s.replace("\\", "\\\\").replace('"', '\\"') for s in lst]
            return "c(" + ", ".join(f'"{s}"' for s in escaped) + ")"

        def _r_num_vec(lst: list[float]) -> str:
            return "c(" + ", ".join(f"{v:.4f}" for v in lst) + ")"

        # ── 散点图（双指标）
        if x_values is not None and y_values is not None:
            grp = groups or ["experiment"] * len(labels)
            return (
                f"df <- data.frame(\n"
                f"  sample = {_r_str_vec(labels)},\n"
                f"  x_val  = {_r_num_vec(x_values)},\n"
                f"  y_val  = {_r_num_vec(y_values)},\n"
                f"  group  = {_r_str_vec(grp)},\n"
                f"  stringsAsFactors = FALSE\n"
                f")\n"
                f'xlabel <- "{xlabel}"\n'
                f'ylabel <- "{ylabel}"\n'
            )

        # ── 多指标矩阵（grouped_bar / 热图）
        if metric_labels is not None and matrix is not None:
            rows = []
            for i, lbl in enumerate(labels):
                row_vals = matrix[i]
                for j, ml in enumerate(metric_labels):
                    rows.append((lbl, ml, row_vals[j]))
            samples_r  = _r_str_vec([r[0] for r in rows])
            metrics_r  = _r_str_vec([r[1] for r in rows])
            values_r   = _r_num_vec([r[2] for r in rows])
            return (
                f"df <- data.frame(\n"
                f"  sample = {samples_r},\n"
                f"  metric = {metrics_r},\n"
                f"  value  = {values_r},\n"
                f"  stringsAsFactors = FALSE\n"
                f")\n"
                f'ylabel <- "{ylabel}"\n'
            )

        # ── 单指标
        vals = values or []
        grp  = groups or _infer_groups(labels)
        return (
            f"df <- data.frame(\n"
            f"  sample = {_r_str_vec(labels)},\n"
            f"  value  = {_r_num_vec(vals)},\n"
            f"  group  = {_r_str_vec(grp)},\n"
            f"  stringsAsFactors = FALSE\n"
            f")\n"
            f'ylabel <- "{ylabel}"\n'
        )

    @staticmethod
    def _inject_project_id(data_r_code: str, project_id: str) -> str:
        """在 data_r_code 末尾追加 project_id_label，供提示词模板使用。"""
        safe = project_id.replace('"', '\\"')
        return data_r_code + f'project_id_label <- "{safe}"\n'

    # ── LLM 调用 ──────────────────────────────────────────────────────────────

    @classmethod
    async def _call_llm(
        cls,
        system_prompt: str,
        user_message: str,
        *,
        use_fallback: bool = False,
    ) -> str | None:
        client = _FB_CLIENT if use_fallback else _LLM_CLIENT
        model  = _FB_MODEL  if use_fallback else _LLM_MODEL
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system",  "content": system_prompt},
                    {"role": "user",    "content": user_message},
                ],
                temperature=0.15,
                max_tokens=4096,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.warning("r_codegen LLM call failed model=%s: %s", model, exc)
            return None

    @classmethod
    def _extract_r_script(cls, text: str | None) -> str | None:
        """从 LLM 输出中提取纯 R 代码（剥去可能的 markdown 围栏）。"""
        if not text:
            return None
        # 去掉 ```r ... ``` 或 ```R ... ```
        m = re.search(r"```[rR]?\n([\s\S]+?)```", text)
        if m:
            return m.group(1).strip()
        # 如果没有围栏，直接取全文（LLM 应该只输出代码）
        stripped = text.strip()
        if stripped.startswith("output_path") or stripped.startswith("library("):
            return stripped
        return stripped  # 尽量返回，让执行阶段报错

    # ── Rscript 执行 ──────────────────────────────────────────────────────────

    @classmethod
    async def _execute_script(
        cls,
        r_script: str,
        output_path: Path,
    ) -> dict[str, Any]:
        """将脚本写入临时文件，subprocess 执行 Rscript，返回 {success, stderr}。"""
        with tempfile.NamedTemporaryFile(
            suffix=".R", mode="w", encoding="utf-8", delete=False
        ) as tf:
            tf.write(r_script)
            script_path = Path(tf.name)

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["Rscript", "--vanilla", str(script_path)],
                capture_output=True,
                text=True,
                timeout=_R_TIMEOUT,
            )
            stderr_text = result.stderr.strip()
            if result.returncode != 0:
                logger.warning(
                    "r_codegen: Rscript 返回码 %d\nstderr:\n%s",
                    result.returncode, stderr_text[:800],
                )
                return {"success": False, "stderr": stderr_text}
            if not output_path.is_file():
                return {
                    "success": False,
                    "stderr": f"Rscript 执行成功但未生成图片文件: {output_path}",
                }
            logger.info("r_codegen: 图片生成成功 %s", output_path)
            return {"success": True, "stderr": stderr_text}
        except subprocess.TimeoutExpired:
            return {"success": False, "stderr": f"Rscript 超时（>{_R_TIMEOUT}s）"}
        except FileNotFoundError:
            return {
                "success": False,
                "stderr": "找不到 Rscript，请先安装 R 并将其加入 PATH",
            }
        finally:
            script_path.unlink(missing_ok=True)

    # ── 主入口 ────────────────────────────────────────────────────────────────

    @classmethod
    async def generate(
        cls,
        *,
        project_id: str,
        metric: str,
        ylabel: str,
        user_request: str,
        labels: list[str],
        values: list[float] | None = None,
        groups: list[str] | None = None,
        # 散点图双指标
        x_values: list[float] | None = None,
        y_values: list[float] | None = None,
        xlabel: str = "",
        # 多指标矩阵
        metric_labels: list[str] | None = None,
        matrix: list[list[float]] | None = None,
    ) -> dict[str, Any]:
        """
        生成 PNG 图片。

        Returns
        -------
        dict — {
            "chart_id":  str,
            "image_url": str,   # /generated/charts/{chart_id}.png
            "r_script":  str,   # 最终执行的 R 脚本（调试用）
        }
        """
        _CHART_DIR.mkdir(parents=True, exist_ok=True)
        chart_id    = uuid4().hex
        output_path = _CHART_DIR / f"{chart_id}.png"

        # ── 1. 构建 R dataframe 代码（末尾注入 project_id_label）
        data_r_code = cls._inject_project_id(
            cls._build_r_dataframe(
                labels, values, ylabel, groups,
                x_values=x_values, y_values=y_values, xlabel=xlabel,
                metric_labels=metric_labels, matrix=matrix,
            ),
            project_id,
        )

        # ── 2. LLM 生成脚本
        user_msg = json.dumps({
            "project_id":  project_id,
            "metric":      metric,
            "ylabel":      ylabel,
            "user_request": user_request,
            "output_path": str(output_path).replace("\\", "/"),
            "data_r_code": data_r_code,
        }, ensure_ascii=False)

        system_prompt = cls._get_codegen_prompt()

        raw = await cls._call_llm(system_prompt, user_msg)
        # 子模型失败 → 主模型
        if not raw and _USE_SUB and SF_API_KEY and SF_BASE_URL:
            logger.info("r_codegen: 子模型无响应，切换主模型")
            raw = await cls._call_llm(system_prompt, user_msg, use_fallback=True)

        r_script = cls._extract_r_script(raw)
        if not r_script:
            raise RuntimeError("LLM 未生成有效 R 脚本")

        # ── 3. 第一次执行
        exec_result = await cls._execute_script(r_script, output_path)

        # ── 4. 失败 → 把 stderr 反馈给 LLM，自动重试一次
        if not exec_result["success"]:
            logger.info("r_codegen: 第一次执行失败，自动重试修复")
            fix_msg = json.dumps({
                "project_id":   project_id,
                "user_request": user_request,
                "output_path":  str(output_path).replace("\\", "/"),
                "original_script": r_script,
                "error_message":   exec_result["stderr"],
                "instruction": (
                    "上面的 R 脚本执行报错，请根据 error_message 修复脚本。"
                    "只输出修复后的完整 R 脚本，不加任何解释。"
                ),
            }, ensure_ascii=False)
            raw2 = await cls._call_llm(system_prompt, fix_msg)
            if not raw2 and _USE_SUB and SF_API_KEY and SF_BASE_URL:
                raw2 = await cls._call_llm(system_prompt, fix_msg, use_fallback=True)

            r_script_fixed = cls._extract_r_script(raw2)
            if r_script_fixed:
                exec_result2 = await cls._execute_script(r_script_fixed, output_path)
                if exec_result2["success"]:
                    r_script = r_script_fixed
                    exec_result = exec_result2
                else:
                    # 两次都失败，抛出详细错误
                    raise RuntimeError(
                        f"R 脚本执行失败（重试后仍失败）：\n{exec_result2['stderr']}"
                    )
            else:
                raise RuntimeError(
                    f"R 脚本执行失败，且 LLM 未能生成修复版本：\n{exec_result['stderr']}"
                )


        return {
            "chart_id":  chart_id,
            "image_url": f"/generated/charts/{chart_id}.png",
            "r_script":  r_script,
        }


def _infer_groups(labels: list[str]) -> list[str]:
    """根据样本名推断分组（取 rep 前的部分，如 T1_rep1 → T1）。"""
    groups = []
    for lbl in labels:
        g = re.sub(r"[_\-]?rep\d+$", "", lbl, flags=re.IGNORECASE).strip("_-")
        groups.append(g or lbl)
    return groups


r_codegen_service = RCodegenService()
