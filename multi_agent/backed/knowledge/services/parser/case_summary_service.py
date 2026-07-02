"""qwen3.7-plus 多模态调用 + 结构化摘要生成服务。"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

from openai import OpenAI

from multi_agent.backed.knowledge.config.settings import settings

logger = logging.getLogger(__name__)

# ── JSON Schema 定义 ─────────────────────────────────────────────────────────

_CASE_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "customer_question": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
            "required": ["text", "source_pages"]
        },
        "sample_or_species": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
            "required": ["text", "source_pages"]
        },
        "reagent_or_method": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
            "required": ["text", "source_pages"]
        },
        "key_observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
                "required": ["text", "source_pages"]
            }
        },
        "analysis_process": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer"},
                    "text": {"type": "string"},
                    "source_pages": {"type": "array", "items": {"type": "integer"}},
                    "analysis_type": {"type": "string", "enum": ["observation", "comparison", "hypothesis", "verification", "inference", "conclusion_support"]}
                },
                "required": ["step", "text", "source_pages", "analysis_type"]
            }
        },
        "key_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_pages": {"type": "array", "items": {"type": "integer"}},
                    "evidence_type": {"type": "string", "enum": ["metric", "comparison", "species_alignment", "sample_quality", "literature_or_background"]}
                },
                "required": ["text", "source_pages", "evidence_type"]
            }
        },
        "conclusion": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
            "required": ["text", "source_pages"]
        },
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
                "required": ["text", "source_pages"]
            }
        },
        "limitations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
                "required": ["text", "source_pages"]
            }
        },
        "rag_keywords": {"type": "array", "items": {"type": "string"}},
        "metadata": {
            "type": "object",
            "properties": {
                "partition_id": {"type": "string"},
                "issue_type": {"type": "string"},
                "species": {"type": "string"},
                "confidence": {"type": "string", "enum": ["case_specific"]}
            },
            "required": ["partition_id", "confidence"]
        }
    },
    "required": ["title", "customer_question", "sample_or_species", "reagent_or_method",
                 "key_observations", "analysis_process", "key_evidence",
                 "conclusion", "recommendations", "limitations", "rag_keywords", "metadata"],
    "additionalProperties": False
}

_REAGENT_MANUAL_SCHEMA = {
    "type": "object",
    "properties": {
        "product_name": {"type": "string"},
        "manufacturer": {"type": "string"},
        "applicable_assays": {"type": "array", "items": {"type": "string"}},
        "key_components": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "function": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
                "required": ["name", "function", "source_pages"]
            }
        },
        "usage_protocol": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"step": {"type": "integer"}, "text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
                "required": ["step", "text", "source_pages"]
            }
        },
        "storage_conditions": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
            "required": ["text", "source_pages"]
        },
        "precautions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
                "required": ["text", "source_pages"]
            }
        },
        "rag_keywords": {"type": "array", "items": {"type": "string"}},
        "metadata": {
            "type": "object",
            "properties": {
                "partition_id": {"type": "string"},
                "catalog_number": {"type": "string"},
                "confidence": {"type": "string", "enum": ["authoritative"]}
            },
            "required": ["partition_id", "confidence"]
        }
    },
    "required": ["product_name", "manufacturer", "applicable_assays", "key_components",
                 "usage_protocol", "storage_conditions", "precautions", "rag_keywords", "metadata"],
    "additionalProperties": False
}

_GENERAL_DOC_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
            "required": ["text", "source_pages"]
        },
        "key_points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
                "required": ["text", "source_pages"]
            }
        },
        "applicable_scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
                "required": ["text", "source_pages"]
            }
        },
        "limitations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}}},
                "required": ["text", "source_pages"]
            }
        },
        "rag_keywords": {"type": "array", "items": {"type": "string"}},
        "metadata": {
            "type": "object",
            "properties": {
                "partition_id": {"type": "string"},
                "confidence": {"type": "string", "enum": ["general_rule"]}
            },
            "required": ["partition_id", "confidence"]
        }
    },
    "required": ["title", "summary", "key_points", "applicable_scenarios", "limitations", "rag_keywords", "metadata"],
    "additionalProperties": False
}

_SCHEMA_MAP = {
    "case_report": ("case_summary", _CASE_REPORT_SCHEMA),
    "reagent_manual": ("reagent_summary", _REAGENT_MANUAL_SCHEMA),
    "general_doc": ("general_summary", _GENERAL_DOC_SCHEMA),
}

# ── Prompts ──────────────────────────────────────────────────────────────────

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "prompts")

def _load_prompt(name: str) -> str:
    path = os.path.join(_PROMPTS_DIR, f"{name}.md")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return ""

_SYSTEM_PROMPTS = {
    "case_report": _load_prompt("parser_case_report"),
    "reagent_manual": _load_prompt("parser_reagent_manual"),
    "general_doc": _load_prompt("parser_general_doc"),
}


class CaseSummaryService:
    def __init__(self):
        self._client: OpenAI | None = None  # 懒加载，避免 API_KEY 未配置时阻断 import

    def _get_client(self) -> OpenAI:
        if self._client is None:
            # PARSER_API_KEY / PARSER_BASE_URL 优先，留空则回退到通用 API_KEY / BASE_URL
            # 注意：os.environ.get() 返回 None 时 pydantic 可能将其序列化为字符串 "None"，
            # 需显式过滤掉无效值，避免 httpx UnsupportedProtocol 错误
            def _clean(val) -> str:
                if not val or not isinstance(val, str):
                    return ""
                s = val.strip()
                return "" if s.lower() in ("none", "null", "") else s

            api_key = _clean(settings.PARSER_API_KEY) or _clean(settings.API_KEY)
            base_url = _clean(settings.PARSER_BASE_URL) or _clean(settings.LLM_BASE_URL) or None

            if not api_key:
                raise RuntimeError(
                    "智能解析需要配置 PARSER_API_KEY 或 API_KEY（知识库服务 .env），请设置后重启服务"
                )
            self._client = OpenAI(api_key=api_key, base_url=base_url)
        return self._client

    def generate_summary_bg(
        self,
        document_id: str,
        doc: dict,
        doc_repo,
        summary_repo,
    ) -> None:
        """后台任务：调用 LLM 生成结构化摘要。"""
        try:
            image_paths: list[str] = doc.get("page_image_paths") or []
            if not image_paths:
                raise ValueError("没有可用的页面图片，请先完成转图步骤")

            schema_type = doc.get("schema_type", "case_report")
            partition_id = doc.get("partition_id", "general")
            file_name = doc.get("file_name", "")
            page_count = len(image_paths)

            draft_json = self._call_llm(
                image_paths=image_paths,
                schema_type=schema_type,
                partition_id=partition_id,
                file_name=file_name,
                page_count=page_count,
            )
            summary_repo.create(document_id, draft_json)
            doc_repo.update_status(document_id, "pending_review")
            logger.info("summary generated document_id=%s schema_type=%s", document_id, schema_type)
        except Exception as exc:
            logger.exception("summary generation failed document_id=%s", document_id)
            doc_repo.update_status(document_id, "summary_failed", parse_error=str(exc))

    def _call_llm(
        self,
        image_paths: list[str],
        schema_type: str,
        partition_id: str,
        file_name: str,
        page_count: int,
    ) -> dict:
        schema_name, json_schema = _SCHEMA_MAP.get(schema_type, _SCHEMA_MAP["case_report"])
        system_prompt = _SYSTEM_PROMPTS.get(schema_type) or _SYSTEM_PROMPTS["case_report"]

        limit = settings.PARSER_BATCH_PAGE_LIMIT
        if page_count <= limit:
            content = self._build_image_content(image_paths)
        else:
            # 超限：只取前 limit 页（简化版，不做章节分批）
            logger.warning("document has %d pages, truncating to %d for LLM", page_count, limit)
            content = self._build_image_content(image_paths[:limit])

        user_prompt_text = (
            f"请按以下步骤完成任务：\n"
            f"第一步：通读所有页面，找出分析、排查、验证、对比、推测等过程性内容（内部思考）\n"
            f"第二步：确认关键现象、证据链和最终结论\n"
            f"第三步：基于以上理解，填写完整 JSON\n\n"
            f"文件名：{file_name}\n总页数：{page_count}\n分区：{partition_id}"
        )
        content.append({"type": "text", "text": user_prompt_text})

        response = self._get_client().chat.completions.create(
            model=settings.PARSER_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": json_schema,
                },
            },
            extra_body={"enable_thinking": False},
        )
        raw = response.choices[0].message.content or "{}"
        return json.loads(raw)

    @staticmethod
    def _build_image_content(image_paths: list[str]) -> list[dict]:
        content = []
        for i, path in enumerate(image_paths, start=1):
            content.append({"type": "text", "text": f"第 {i} 页："})
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
        return content
