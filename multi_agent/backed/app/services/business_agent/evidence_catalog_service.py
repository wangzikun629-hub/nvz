from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    metric_schema_service,
)


class EvidenceCatalogService:
    """Index project evidence by content metadata before question-specific reading."""

    VERSION = "evidence-catalog-v1"
    MAX_FILES = 10000
    MAX_HEADER_BYTES = 2_000_000
    TABLE_SUFFIXES = {".csv", ".tsv", ".tab", ".txt", ".xls"}
    SAMPLE_HEADERS = {
        "sample",
        "sampleid",
        "sample_id",
        "samplename",
        "file",
    }
    HEADER_METRICS = {
        "totalrawreads": {"sequencing_depth"},
        "rawreads": {"sequencing_depth"},
        "totalcleanreads": {"sequencing_depth"},
        "cleanreads": {"sequencing_depth"},
        "mappingrate": {"mapping_rate_percent"},
        "mapping": {"mapping_rate_percent"},
        "uniquemappedrate": {"unique_mapping_rate_percent"},
        "uniquemappingrate": {"unique_mapping_rate_percent"},
        "unique": {"unique_mapping_rate_percent"},
        "picardduplicationrate": {"duplicate_rate_percent"},
        "duplicaterate": {"duplicate_rate_percent"},
        "mtratio": {"mt_rate_percent"},
        "chrmtptrate": {"mt_rate_percent"},
        "nrf": {"nrf"},
        "pbc1": {"pbc1"},
        "pbc2": {"pbc2"},
        "frip": {"frip_ratio"},
        "readsinpeaks": {"frip_ratio"},
        "featurereadcount": {"frip_ratio"},
        "peakcount": {"peak_count"},
        "peaksnumber": {"peak_count"},
    }

    @classmethod
    def build(cls, root: Path) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        metric_index: dict[str, list[str]] = {}
        sample_index: dict[str, list[str]] = {}
        parse_status_counts: dict[str, int] = {}
        truncated = False

        try:
            paths = sorted(path for path in root.rglob("*") if path.is_file())
        except OSError:
            paths = []
        if len(paths) > cls.MAX_FILES:
            paths = paths[: cls.MAX_FILES]
            truncated = True

        for path in paths:
            record = cls._index_file(root, path)
            records.append(record)
            status = str(record.get("parse_status") or "unknown")
            parse_status_counts[status] = parse_status_counts.get(status, 0) + 1
            relative = str(record.get("path") or "")
            for metric_id in record.get("metric_ids", []) or []:
                metric_index.setdefault(metric_id, []).append(relative)
            for sample in record.get("samples", []) or []:
                sample_index.setdefault(sample, []).append(relative)

        return {
            "version": cls.VERSION,
            "root": str(root),
            "file_count": len(records),
            "truncated": truncated,
            "parse_status_counts": parse_status_counts,
            "metric_index": metric_index,
            "sample_index": sample_index,
            "files": records,
        }

    @classmethod
    def query(
        cls,
        catalog: dict[str, Any],
        *,
        metric_ids: list[str] | tuple[str, ...] | set[str] | None = None,
        samples: list[str] | tuple[str, ...] | set[str] | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        wanted_metrics = {
            metric_schema_service.canonical_id(item)
            for item in (metric_ids or [])
            if str(item or "").strip()
        }
        wanted_samples = {str(item) for item in (samples or []) if str(item).strip()}
        result: list[dict[str, Any]] = []
        for record in catalog.get("files", []) or []:
            if not isinstance(record, dict):
                continue
            record_metrics = set(record.get("metric_ids", []) or [])
            record_samples = set(record.get("samples", []) or [])
            if wanted_metrics and not wanted_metrics.intersection(record_metrics):
                continue
            if wanted_samples and record_samples and not wanted_samples.intersection(record_samples):
                continue
            result.append(record)
            if len(result) >= limit:
                break
        return result

    @classmethod
    def paths_for_metrics(
        cls,
        root: Path,
        catalog: dict[str, Any],
        metric_ids: list[str] | tuple[str, ...] | set[str],
        *,
        limit: int = 20,
    ) -> list[Path]:
        selected: list[Path] = []
        seen: set[Path] = set()
        canonical_metrics = list(
            dict.fromkeys(
                metric_schema_service.canonical_id(item)
                for item in metric_ids
                if str(item or "").strip()
            )
        )
        per_metric_limit = max(1, min(3, limit // max(1, len(canonical_metrics))))
        for metric_id in canonical_metrics:
            records = cls.query(catalog, metric_ids=[metric_id], limit=100)
            records.sort(key=cls._record_priority)
            added_for_metric = 0
            for record in records:
                relative = str(record.get("path") or "")
                candidate = (root / relative).resolve()
                if candidate in seen or not candidate.is_file():
                    continue
                seen.add(candidate)
                selected.append(candidate)
                added_for_metric += 1
                if len(selected) >= limit or added_for_metric >= per_metric_limit:
                    break
            if len(selected) >= limit:
                break
        return selected

    @classmethod
    def summary_for_context(cls, catalog: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": catalog.get("version"),
            "file_count": catalog.get("file_count", 0),
            "truncated": catalog.get("truncated", False),
            "parse_status_counts": catalog.get("parse_status_counts", {}),
            "indexed_metrics": sorted((catalog.get("metric_index") or {}).keys()),
            "indexed_sample_count": len(catalog.get("sample_index") or {}),
        }

    @classmethod
    def _index_file(cls, root: Path, path: Path) -> dict[str, Any]:
        try:
            relative = str(path.relative_to(root)).replace("\\", "/")
        except ValueError:
            relative = str(path)
        try:
            stat_result = path.stat()
            size = stat_result.st_size
            mtime_ns = stat_result.st_mtime_ns
        except OSError:
            size = 0
            mtime_ns = 0
        stage = cls._stage(path)
        record: dict[str, Any] = {
            "path": relative,
            "name": path.name,
            "suffix": path.suffix.lower(),
            "size_bytes": size,
            "mtime_ns": mtime_ns,
            "stage": stage,
            "parse_status": "indexed_binary",
            "headers": [],
            "samples": [],
            "metric_ids": sorted(cls._metrics_from_stage(stage)),
        }
        if path.suffix.lower() not in cls.TABLE_SUFFIXES or size > cls.MAX_HEADER_BYTES:
            return record
        try:
            text = cls._read_prefix(path)
        except OSError as exc:
            record["parse_status"] = "unreadable"
            record["parse_error"] = str(exc)
            return record
        lines = [line for line in text.splitlines() if line.strip()][:80]
        if not lines:
            record["parse_status"] = "empty"
            return record
        delimiter = "\t" if lines[0].count("\t") >= lines[0].count(",") else ","
        first = [part.strip().strip('"') for part in lines[0].split(delimiter)]
        has_header = cls._looks_like_header(first)
        headers = first if has_header else []
        data_lines = lines[1:] if has_header else lines
        sample_index = cls._sample_column(headers)
        samples: list[str] = []
        for line in data_lines[:50]:
            parts = [part.strip().strip('"') for part in line.split(delimiter)]
            if not has_header and not (
                path.name.lower().startswith("samplelist")
                or (len(parts) > 1 and cls._looks_like_path(parts[1]))
            ):
                continue
            index = sample_index if sample_index is not None else 0
            if index >= len(parts):
                continue
            sample = parts[index]
            if sample and not cls._looks_like_path(sample) and sample not in samples:
                samples.append(sample)
        metric_ids = cls._metrics_from_headers(headers)
        metric_ids.update(cls._metrics_from_stage(str(record.get("stage") or "")))
        stage = str(record.get("stage") or "")
        if stage == "spikein_alignment_normalization":
            normalized_headers = {
                cls._normalize_header(item) for item in headers
            }
            if "unique_mapping_rate_percent" in metric_ids:
                metric_ids.remove("unique_mapping_rate_percent")
                metric_ids.add("spikein_unique_mapping_rate_percent")
            if "mappedreads" in normalized_headers:
                metric_ids.add("spikein_mapped_reads")
            if normalized_headers.intersection(
                {"scalingfactor", "scalefactor", "normalizationfactor"}
            ):
                metric_ids.add("spikein_scaling_factor")
        if stage == "cross_sample_comparison":
            metric_ids.add("correlation")
        record.update(
            {
                "parse_status": "parsed_header" if has_header else "parsed_rows_no_header",
                "headers": headers[:80],
                "samples": samples[:50],
                "metric_ids": sorted(metric_ids),
            }
        )
        return record

    @staticmethod
    def _read_prefix(path: Path) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin1"):
            try:
                with path.open("r", encoding=encoding, errors="strict") as handle:
                    return handle.read(128_000)
            except UnicodeError:
                continue
        return path.read_text(encoding="utf-8", errors="replace")[:128_000]

    @classmethod
    def _metrics_from_headers(cls, headers: list[str]) -> set[str]:
        normalized = {cls._normalize_header(header) for header in headers}
        metrics: set[str] = set()
        for header in normalized:
            metrics.update(cls.HEADER_METRICS.get(header, set()))
            if "uniquemappingrate" in header:
                metrics.add(
                    "spikein_unique_mapping_rate_percent"
                    if any("spike" in cls._normalize_header(item) for item in headers)
                    else "unique_mapping_rate_percent"
                )
        if {"frip", "percent"}.intersection(normalized) and (
            {"readsinpeaks", "featurereadcount", "peakcount"}.intersection(normalized)
        ):
            metrics.add("frip_ratio")
        return metrics

    @classmethod
    def _looks_like_header(cls, parts: list[str]) -> bool:
        normalized = {cls._normalize_header(part) for part in parts}
        known = set(cls.HEADER_METRICS)
        known.update(cls.SAMPLE_HEADERS)
        return bool(normalized.intersection(known)) or any(
            token in normalized
            for token in ("sample", "sampleid", "sample_id", "file", "featuretype")
        )

    @classmethod
    def _sample_column(cls, headers: list[str]) -> int | None:
        for index, header in enumerate(headers):
            if cls._normalize_header(header) in cls.SAMPLE_HEADERS:
                return index
        return None

    @staticmethod
    def _normalize_header(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

    @staticmethod
    def _looks_like_path(value: str) -> bool:
        lowered = value.lower()
        return "/" in value or "\\" in value or lowered.endswith((".fq", ".fq.gz", ".fastq", ".fastq.gz"))

    @staticmethod
    def _stage(path: Path) -> str:
        text = str(path).lower().replace("\\", "/")
        rules = (
            ("raw", "raw_reads"),
            ("readsqc", "reads_qc"),
            ("trim", "reads_qc"),
            ("spike", "spikein_alignment_normalization"),
            ("frip", "post_peak_calling_enrichment"),
            ("correlation", "cross_sample_comparison"),
            ("spearman", "cross_sample_comparison"),
            ("fragment", "fragment_size_analysis"),
            ("insert_size", "fragment_size_analysis"),
            ("motif", "motif_analysis"),
            ("alignment", "host_alignment"),
            ("peak", "peak_calling"),
            ("diff", "differential_analysis"),
        )
        for token, stage in rules:
            if token in text:
                return stage
        return "unclassified"

    @staticmethod
    def _metrics_from_stage(stage: str) -> set[str]:
        return {
            "reads_qc": {"sequencing_depth"},
            "spikein_alignment_normalization": {
                "spikein_mapped_reads",
                "spikein_unique_mapping_rate_percent",
            },
            "host_alignment": {
                "mapping_rate_percent",
                "unique_mapping_rate_percent",
                "mt_rate_percent",
            },
            "peak_calling": {"peak_count"},
            "post_peak_calling_enrichment": {"frip_ratio"},
            "cross_sample_comparison": {"correlation"},
            "fragment_size_analysis": {"fragment_size"},
            "motif_analysis": {"motif"},
        }.get(stage, set())

    @staticmethod
    def _record_priority(record: dict[str, Any]) -> tuple[int, int, int, str]:
        name = str(record.get("name") or "").lower()
        preferred_names = {
            "readsqc.xls",
            "alignmentqc.xls",
            "spikein_align.xls",
            "frip_score.xls",
            "frip_raw.txt",
            "samples_peak_number_stat.xls",
            "spearman_corr_readcounts.tab",
        }
        path = str(record.get("path") or "")
        return (
            0 if name in preferred_names else 1,
            0 if record.get("parse_status") == "parsed_header" else 1,
            path.count("/"),
            path,
        )


evidence_catalog_service = EvidenceCatalogService()
