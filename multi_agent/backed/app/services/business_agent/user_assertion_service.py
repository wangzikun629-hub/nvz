from __future__ import annotations

import re
from typing import Any


class UserAssertionService:
    """Extract metric-bearing user statements without promoting them to project facts."""

    VERSION = "user-assertions-v1"
    METRIC_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("frip_ratio", ("frip",)),
        ("peak_count", ("peak", "peaks", "峰数量", "峰数")),
        ("correlation", ("correlation", "spearman", "pearson", "相关性", "相关系数")),
        ("mapping_rate_percent", ("mapping rate", "mapping", "比对率")),
        (
            "unique_mapping_rate_percent",
            ("unique mapping", "unique rate", "唯一比对", "唯一比对率"),
        ),
        (
            "spikein_unique_mapping_rate_percent",
            ("spike-in unique", "spikein unique", "spike-in唯一比对"),
        ),
        (
            "spikein_scaling_factor",
            ("scaling factor", "scale factor", "normalization factor", "缩放因子"),
        ),
        ("spikein_mapped_reads", ("spike-in mapped", "spikein mapped")),
        ("fragment_size", ("fragment size", "insert size", "片段长度")),
        ("nrf", ("nrf",)),
        ("pbc1", ("pbc1",)),
        ("pbc2", ("pbc2",)),
        ("motif", ("motif", "基序")),
    )
    NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d[\d,]*(?:\.\d+)?\s*%?")

    @classmethod
    def extract(
        cls,
        question: str,
        *,
        known_samples: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        assertions: list[dict[str, Any]] = []
        samples = [str(item) for item in (known_samples or []) if str(item).strip()]
        segments = [
            segment.strip()
            for segment in re.split(r"(?<=[。！？!?；;\n])", str(question or ""))
            if segment.strip()
        ]
        for segment_index, segment in enumerate(segments):
            lowered = segment.lower()
            values = cls._values(segment)
            if not values:
                continue
            mentioned_samples = [sample for sample in samples if sample.lower() in lowered]
            for metric_id, terms in cls.METRIC_TERMS:
                if not any(term.lower() in lowered for term in terms):
                    continue
                assertions.append(
                    {
                        "assertion_id": f"UA-{segment_index + 1}-{metric_id}",
                        "metric_id": metric_id,
                        "text": segment,
                        "values": values,
                        "samples": mentioned_samples,
                        "provenance": "user_provided",
                        "verification": "unverified",
                        "allowed_usage": "conditional_reasoning",
                    }
                )
        return assertions

    @classmethod
    def _values(cls, text: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for match in cls.NUMBER.finditer(text):
            raw = match.group(0).strip()
            normalized = raw.replace(",", "").rstrip("%").strip()
            try:
                value = float(normalized)
            except ValueError:
                continue
            result.append(
                {
                    "value": int(value) if value.is_integer() else value,
                    "raw": raw,
                    "unit": "%" if raw.endswith("%") else "",
                }
            )
        return result[:12]


user_assertion_service = UserAssertionService()
