from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.infrastructure.tools.local.project_reader import (
    find_files,
    find_internal_workflow_files,
    list_report_roots,
    read_text_snippet,
)
from multi_agent.backed.app.services.business_agent.metric_schema_service import metric_schema_service
from multi_agent.backed.app.services.business_agent.experiment_design_service import experiment_design_service
from multi_agent.backed.app.services.business_agent.evidence_catalog_service import evidence_catalog_service
from multi_agent.backed.app.services.project_analysis_constants import PREFLIGHT_CONFIG_KEYS


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag.lower() in {"br", "p", "div", "section", "article", "tr", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag.lower() in {"p", "div", "section", "article", "tr", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = " ".join(data.split())
        if text:
            self.parts.append(text)

    def text(self) -> str:
        lines = []
        for line in "\n".join(self.parts).splitlines():
            cleaned = " ".join(line.split())
            if cleaned:
                lines.append(cleaned)
        return "\n".join(lines)


# Config sections/keys to handle during YAML extraction
_CONFIG_NESTED_SECTIONS = {"deeptools_params", "threads"}
_CONFIG_SKIP_SECTIONS = {"software"}
_CONFIG_SKIP_KEYS = {
    "genome_size_file",
    "peak_go_term2gene_relpath",
    "go_name",
    "kegg_name",
}


class ProjectContextBuilderService:

    @staticmethod
    def relative_path(root: Path, path: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)

    @classmethod
    def parse_samplelist(cls, path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        text = path.read_text(encoding="utf-8", errors="ignore")
        content_lines = [
            raw_line.strip()
            for raw_line in text.splitlines()
            if raw_line.strip() and not raw_line.strip().startswith("#")
        ]
        if not content_lines:
            return rows
        first_parts = [part.strip() for part in content_lines[0].split("\t")]
        if len(first_parts) < 2:
            first_parts = content_lines[0].split()
        normalized_headers = [re.sub(r"[^a-z0-9]+", "_", item.lower()).strip("_") for item in first_parts]
        known_headers = {"sample", "sample_id", "fastq_1", "fastq1", "condition", "replicate", "target", "role", "control_for", "batch"}
        has_header = bool(set(normalized_headers) & known_headers)
        data_lines = content_lines[1:] if has_header else content_lines
        for raw_line in data_lines:
            line = raw_line.strip()
            parts = [part.strip() for part in line.split("\t")]
            if len(parts) < 2:
                parts = line.split()
            if not parts:
                continue
            mapped = {
                normalized_headers[index]: value
                for index, value in enumerate(parts)
                if has_header and index < len(normalized_headers)
            }
            sample = mapped.get("sample") or mapped.get("sample_id") or parts[0]
            design_fields = {
                key: mapped.get(key, "")
                for key in ("condition", "replicate", "target", "role", "control_for", "batch")
                if mapped.get(key, "") not in (None, "")
            }
            row = {
                "sample": sample,
                "fastq_1": mapped.get("fastq_1") or mapped.get("fastq1") or (parts[1] if len(parts) > 1 else ""),
                "fastq_2": mapped.get("fastq_2") or mapped.get("fastq2") or (parts[2] if len(parts) > 2 else ""),
                "peak_type": mapped.get("peak_type") or (parts[3] if len(parts) > 3 else ""),
                "control_sample": mapped.get("control") or (
                    parts[4] if not has_header and len(parts) > 4 else ""
                ),
                "design_fields": design_fields,
                "raw_fields": parts,
            }
            rows.append(row)
        return rows

    @classmethod
    def extract_yaml_config_fields(cls, raw: dict) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                continue
            if key in _CONFIG_SKIP_SECTIONS or key in _CONFIG_SKIP_KEYS:
                continue
            if isinstance(value, dict):
                if key in _CONFIG_NESTED_SECTIONS:
                    for sub_key, sub_val in value.items():
                        if isinstance(sub_val, (str, int, float, bool)):
                            result[f"{key}.{sub_key}"] = str(sub_val)
                elif key == "adapter_sets":
                    for adapter_type, tool_dict in value.items():
                        if not isinstance(tool_dict, dict):
                            continue
                        for tool, mode_dict in tool_dict.items():
                            if not isinstance(mode_dict, dict):
                                continue
                            for mode, cmd in mode_dict.items():
                                result[f"adapter_sets.{adapter_type}.{mode}"] = str(cmd)[:240]
                else:
                    pairs = "; ".join(
                        f"{k}={v}"
                        for k, v in value.items()
                        if isinstance(v, (str, int, float, bool))
                    )
                    if pairs:
                        result[key] = pairs[:300]
            elif isinstance(value, list):
                result[key] = ", ".join(str(item) for item in value)[:200]
            else:
                str_val = "" if value is None else str(value)
                result[key] = str_val
                if key == "samplist" and "samplelist" not in result:
                    result["samplelist"] = str_val
        return result

    @classmethod
    def parse_config_summary(cls, path: Path) -> dict[str, str]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            import yaml
            raw = yaml.safe_load(text)
            if isinstance(raw, dict):
                result = cls.extract_yaml_config_fields(raw)
                logger.info("config_parse path=%s parser=yaml keys=%d", path, len(result))
                return result
            logger.warning("config_parse path=%s parser=yaml result_not_dict type=%s", path, type(raw))
        except ImportError:
            logger.warning("config_parse path=%s parser=yaml_unavailable fallback=line_by_line", path)
        except Exception as exc:
            logger.warning("config_parse path=%s parser=yaml_failed error=%s fallback=line_by_line", path, exc)

        summary: dict[str, str] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().strip("'\"")
            if key not in PREFLIGHT_CONFIG_KEYS and key != "samplist":
                continue
            cleaned_value = value.strip()
            if cleaned_value.startswith(("'", '"')):
                quote_char = cleaned_value[0]
                end_index = cleaned_value.find(quote_char, 1)
                if end_index > 0:
                    cleaned_value = cleaned_value[: end_index + 1]
            elif "#" in cleaned_value:
                cleaned_value = cleaned_value.split("#", 1)[0].strip()
            cleaned_value = cleaned_value.strip().strip("'\"")
            norm_key = "samplelist" if key == "samplist" else key
            summary[norm_key] = cleaned_value
        return summary

    @staticmethod
    def infer_sample_role(sample_name: str, raw_fields: list[str] | None = None) -> str:
        tokens = " ".join([sample_name or "", *[str(item) for item in (raw_fields or [])]]).lower()
        if any(token in tokens for token in ("igg", "isotype", "negative")):
            return "IgG/control"
        if any(token in tokens for token in ("input", "inputdna", "input_dna")):
            return "Input"
        if any(token in tokens for token in ("control", "ctrl", "mock", "vehicle")):
            return "Control"
        if any(token in tokens for token in ("treat", "case", "ko", "oe", "stim")):
            return "Treatment"
        return "Experimental"

    @classmethod
    def build_sample_roles(cls, samples: list[dict[str, Any]]) -> list[dict[str, str]]:
        roles: list[dict[str, str]] = []
        for item in samples:
            sample = str(item.get("sample") or "").strip()
            if not sample:
                continue
            roles.append(
                {
                    "sample": sample,
                    "role": cls.infer_sample_role(sample, item.get("raw_fields") or []),
                    "basis": "samplelist/name heuristic",
                }
            )
        return roles

    @classmethod
    def build_workflow_summary(
        cls,
        root: Path,
        limit: int = 10,
        project_config: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        files: list[dict[str, str]] = []
        detected_parameters: dict[str, str] = {}
        code_formula_sources: dict[str, dict[str, str]] = {}
        keyword_patterns = {
            "aligner": r"\b(bowtie2|bwa|hisat2|star)\b",
            "peak_caller": r"\b(macs2|macs3|seacr)\b",
            "duplicate_handling": r"\b(remove_duplicates|markdup|rmdup|dedup|picard)\b",
            "organelle_filter": r"\b(organelle_chroms|chrM|chrMT|Pt|chloroplast|mitochond)\b",
            "reference_genome": r"\b(species|genome|reference|fasta|hg38|hg19|mm10|tair|grch)\b",
            "frip": r"\b(FRiP|frip|reads.*peak|peak.*reads)\b",
        }
        metric_formula_patterns = {
            "adapter_percent": r"(adapter[^=\n]{0,40}=\s*[^\n]+|adapter[^/\n]{0,60}/[^\n]+)",
            "q30_ratio": r"(q30[^=\n]{0,40}=\s*[^\n]+|Q\s*>=\s*30|qual[^/\n]{0,60}/[^\n]+)",
            "mapping_rate_percent": r"(mapping[^=\n]{0,40}=\s*[^\n]+|map[^/\n]{0,80}/[^\n]+)",
            "unique_mapping_rate_percent": r"(unique[^=\n]{0,40}=\s*[^\n]+|uniq[^/\n]{0,80}/[^\n]+)",
            "duplicate_rate_percent": r"(duplicate[^=\n]{0,40}=\s*[^\n]+|dup[^/\n]{0,80}/[^\n]+)",
            "mt_rate_percent": r"((?:chrMT|chrM|organelle|mitochond|chloroplast|chrPt)[^=\n]{0,60}=\s*[^\n]+|(?:chrMT|chrM|organelle|mitochond|chloroplast|chrPt)[^/\n]{0,80}/[^\n]+)",
            "frip_ratio": r"(frip[^=\n]{0,40}=\s*[^\n]+|reads[^/\n]{0,40}peaks[^/\n]{0,80}/[^\n]+|peak[^/\n]{0,40}reads[^/\n]{0,80}/[^\n]+)",
            "correlation": r"(spearman[^=\n]{0,40}=\s*[^\n]+|correlation[^=\n]{0,40}=\s*[^\n]+)",
        }

        def read_workflow_text(path: Path) -> str:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return ""
            return text[:50000]

        def logical_lines(text: str) -> list[tuple[int, str]]:
            rows: list[tuple[int, str]] = []
            pending = ""
            start_line = 1
            for line_no, raw in enumerate(text.splitlines(), start=1):
                stripped = raw.strip()
                if not stripped or stripped.startswith(("#", "//")):
                    continue
                if not pending:
                    start_line = line_no
                pending = f"{pending} {stripped}".strip()
                if stripped.endswith(("\\", "+", "%>%", "|>", ",")):
                    continue
                rows.append((start_line, pending))
                pending = ""
            if pending:
                rows.append((start_line, pending))
            return rows

        def is_formula_like(line: str) -> bool:
            code_only = re.sub(r"(['\"]).*?\1", "", line)
            lowered = code_only.lower()
            if any(token in lowered for token in ("read.table", "read_csv", "read.delim", "fread", "open(", "file.path")):
                return False
            has_assignment = bool(re.search(r"(<-|=|:=)", code_only))
            has_ratio = "/" in code_only or "percent" in lowered or "rate" in lowered or "ratio" in lowered
            has_compute_call = any(token in lowered for token in ("mutate(", "summarise(", "awk", "bc", "scale=", "corr(", "cor("))
            return has_assignment and (has_ratio or has_compute_call)

        def extract_formula(metric_key: str, text: str) -> tuple[str, int] | None:
            code_patterns = {
                "mapping_rate_percent": (
                    r"metrics\[['\"]Mapping_Rate['\"]\]\s*=\s*[^\n]+",
                    r"overall alignment rate",
                ),
                "unique_mapping_rate_percent": (
                    r"metrics\[['\"]Unique_Mapped_Rate['\"]\]\s*=\s*[^\n]+",
                    r"aligned concordantly exactly 1 time",
                ),
                "duplicate_rate_percent": (
                    r"metrics\[f?['\"][^'\"]*Duplication_Rate['\"]\]\s*=\s*[^\n]+",
                    r"PERCENT_DUPLICATION",
                ),
                "frip_ratio": (
                    r"target_df\.columns\s*=\s*\[[^\n]*['\"]FRiP['\"][^\n]*\]",
                    r"plotEnrichment[^\n]+--outRawCounts",
                ),
            }.get(metric_key, ())
            for pattern in code_patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if not match:
                    continue
                line_no = text[: match.start()].count("\n") + 1
                line = " ".join(match.group(0).strip().split())
                if metric_key == "frip_ratio" and "target_df.columns" in line:
                    line = f"{line}; FRiP is read from plotEnrichment --outRawCounts after retaining matching BAM/peak rows"
                if metric_key == "mapping_rate_percent" and "overall alignment rate" in line.lower():
                    line = "Mapping_Rate is parsed from bowtie2 'overall alignment rate' line"
                if metric_key == "unique_mapping_rate_percent" and "aligned concordantly exactly 1 time" in line.lower():
                    line = "Unique_Mapped_Rate is parsed from bowtie2 'aligned concordantly exactly 1 time' line"
                if metric_key == "duplicate_rate_percent" and "percent_duplication" in line.upper():
                    line = "Picard_Duplication_Rate is derived from Picard PERCENT_DUPLICATION * 100"
                return line[:320], line_no

            terms = {
                "adapter_percent": ("adapter",),
                "q30_ratio": ("q30", "qual", "quality"),
                "mapping_rate_percent": ("mapping", "map_rate", "mapped", "alignment_rate"),
                "unique_mapping_rate_percent": ("unique", "uniq"),
                "duplicate_rate_percent": ("duplicate", "duplication", "dup_rate", "dup"),
                "mt_rate_percent": ("chrmt", "chrm", "organelle", "mitochond", "chloroplast", "chrpt"),
                "frip_ratio": ("frip", "reads_in_peak", "reads_in_peaks", "in_peaks"),
                "correlation": ("spearman", "correlation", "corr"),
            }.get(metric_key, ())
            for line_no, line in logical_lines(text):
                lowered = line.lower()
                if not any(term in lowered for term in terms):
                    continue
                if not is_formula_like(line):
                    continue
                return " ".join(line.split())[:320], line_no
            return None

        for path in find_internal_workflow_files(root, limit=limit, project_config=project_config):
            text = read_workflow_text(path)
            if not text:
                continue
            lower_text = text.lower()
            matched = []
            is_code_file = path.suffix.lower() in {
                ".py", ".r", ".rmd", ".sh", ".bash", ".awk", ".sed", ".pl", ".smk", ".rule", ".rules",
            } or path.name.lower() == "snakefile"
            if is_code_file:
                for metric_key, pattern in metric_formula_patterns.items():
                    if metric_key in code_formula_sources:
                        continue
                    extracted = extract_formula(metric_key, text)
                    if extracted:
                        formula, source_line = extracted
                    else:
                        match = re.search(pattern, text, flags=re.IGNORECASE)
                        if not match:
                            continue
                        if not is_formula_like(match.group(0)):
                            continue
                        formula = " ".join(match.group(0).strip().split())[:320]
                        source_line = text[: match.start()].count("\n") + 1
                    code_formula_sources[metric_key] = {
                        "formula": formula,
                        "source_file": cls.relative_path(root, path),
                        "source_line": str(source_line),
                    }
            for key, pattern in keyword_patterns.items():
                if re.search(pattern, text, flags=re.IGNORECASE):
                    matched.append(key)
                    if key not in detected_parameters:
                        line = next(
                            (
                                raw.strip()
                                for raw in text.splitlines()
                                if re.search(pattern, raw, flags=re.IGNORECASE)
                            ),
                            "",
                        )
                        detected_parameters[key] = line[:240]
            files.append(
                {
                    "file": cls.relative_path(root, path),
                    "type": path.suffix.lower() or path.name,
                    "matched_topics": ", ".join(matched),
                    "has_shell_command": str(any(cmd in lower_text for cmd in ("bowtie2", "macs", "samtools", "bedtools"))),
                }
            )
        return {
            "files": files,
            "detected_parameters": detected_parameters,
            "code_formula_sources": code_formula_sources,
            "file_count": len(files),
        }

    @classmethod
    def organelle_semantics(cls, payload: dict[str, Any] | None) -> dict[str, str]:
        payload = payload or {}
        config = payload.get("config") if isinstance(payload.get("config"), dict) else payload
        species = str(
            config.get("species") or config.get("genome") or config.get("reference") or ""
        ).strip()
        normalized = species.lower()
        plant_tokens = ("tair", "arabidopsis", "oryza", "rice", "zea", "maize", "tomato", "solanum", "wheat", "triticum", "brassica", "plant")
        animal_tokens = ("hg", "grch", "human", "mm", "grcm", "mouse", "rn", "rat", "danrer", "zebrafish", "bos", "sus", "canfam")
        if any(token in normalized for token in plant_tokens):
            return {
                "species": species,
                "label": "Mitochondrial/plastid alignment rate",
                "table_label": "chrMT/Pt(%)",
                "definition": "比对到线粒体或叶绿体/质体染色体的 reads 比例。",
                "assumption": "植物样本需结合 organelle_chroms 确认 chrMT 与 Pt/chrPt 的实际染色体命名和过滤策略。",
                "interpretation": "该值描述植物细胞器 reads 占比，需要结合核基因组有效 reads 和流程过滤策略解释。",
                "downstream_impact": "若细胞器 reads 占用较多已比对 reads，可能减少可用于核基因组信号分析的 reads。",
            }
        if any(token in normalized for token in animal_tokens):
            return {
                "species": species,
                "label": "Mitochondrial alignment rate",
                "table_label": "Mitochondrial(%)",
                "definition": "比对到线粒体染色体的 reads 比例。",
                "assumption": "当前参考基因组属于动物/人类体系，应按 chrM/MT 及项目配置核对该指标。",
                "interpretation": "该值描述线粒体 reads 占比，需要结合参考基因组、chrM/MT 命名和过滤策略解释。",
                "downstream_impact": "若线粒体 reads 占用较多已比对 reads，可能减少可用于核基因组信号分析的 reads。",
            }
        return {
            "species": species,
            "label": "Organelle alignment rate",
            "table_label": "Organelle(%)",
            "definition": "比对到项目所配置细胞器染色体的 reads 比例。",
            "assumption": "物种信息不足时不能自行判断该字段代表线粒体还是叶绿体/质体。",
            "interpretation": "需先确认 species/reference 和 organelle_chroms，再解释该指标。",
            "downstream_impact": "需先确认物种和细胞器染色体口径，才能判断其对核基因组有效 reads 的影响。",
        }

    @classmethod
    def metric_glossary(cls, config: dict[str, Any] | None = None) -> dict[str, str]:
        organelle = cls.organelle_semantics(config)
        return {
            "samplelist": "Defines sample IDs and input FASTQ paths. This should be read before interpreting all downstream metrics.",
            "config.yaml": "Pipeline parameters, species/reference choice, output path, duplicate handling, spike-in setting and peak calling thresholds.",
            "ReadsQC.Total Clean Reads": "Reads retained after filtering. Low retention can indicate raw sequencing or trimming problems.",
            "ReadsQC.Adapter": "Fraction of raw reads in which adapter-related sequence was detected. It does not by itself prove adapter remains in clean reads after trimming.",
            "ReadsQC.Q20/Q30": "Base quality ratios. Lower Q30 usually points to sequencing quality issues.",
            "AlignmentQC.Mapping_Rate": "Fraction of reads mapped to the reference genome. Low values can suggest species/reference mismatch or poor read quality.",
            "AlignmentQC.Unique_Mapped_Rate": "Uniquely mapped read ratio. Low values can indicate repetitive reads or reference issues.",
            "AlignmentQC.Picard_Duplication_Rate": "PCR/optical duplication estimate. High duplication can reduce usable library complexity.",
            "AlignmentQC.MT_Ratio": f"{organelle['definition']} {organelle['assumption']}",
            "Correlation.Spearman": "Sample similarity based on genome-wide signal. Replicates should generally correlate better than unrelated controls.",
            "Peak.FRIP": "Reads in peaks fraction. It reflects enrichment strength and should be interpreted with peak count and control samples.",
            "Peak.PeakCount": "Number of called peaks. Very high control peak counts or very low target peaks need review.",
            "Motif.knownResults": "Known motif enrichment from peak sequences. Top motifs should match target biology or expected cofactors where possible.",
        }

    @classmethod
    def read_metric_guides(cls, root: Path, report_roots: list[Path]) -> list[dict[str, str]]:
        relative_candidates = [
            "README.txt",
            "1.ReadsQC/README.txt",
            "2.AlignmentQC/README.txt",
            "2.AlignmentQC/2.1AlignmentStat/README.txt",
            "3.Correlation/README.txt",
            "5.Peakcalling/README.txt",
            "5.Peakcalling/5.1PeakStat/README.txt",
            "5.Peakcalling/5.3PeakAnno/README.txt",
            "7.Motif/README.txt",
            "8.MotifyAnalysis/README.txt",
        ]
        candidates: list[Path] = [root / "README.txt"]
        for report_root in report_roots:
            candidates.extend(report_root / relative for relative in relative_candidates)

        guides: list[dict[str, str]] = []
        seen: set[Path] = set()
        for path in candidates:
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError):
                resolved = path
            if resolved in seen or not path.exists() or not path.is_file():
                continue
            seen.add(resolved)
            try:
                preview = read_text_snippet(path, max_lines=120, max_chars=12000)
            except OSError:
                continue
            guides.append(
                {
                    "file": cls.relative_path(root, path),
                    "section": path.parent.name,
                    "preview": preview[:3000],
                }
            )
            if len(guides) >= 12:
                break
        return guides

    @classmethod
    def build_metric_rule_sources(
        cls,
        *,
        metric_guides: list[dict[str, str]],
        workflow_summary: dict[str, Any],
        config: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        searchable_blocks: list[tuple[str, str, str]] = []
        for guide in metric_guides:
            searchable_blocks.append(("report_readme", str(guide.get("file", "")), str(guide.get("preview", ""))))
        for key, value in (workflow_summary.get("detected_parameters", {}) or {}).items():
            searchable_blocks.append(("project_workflow", key, str(value)))
        if config:
            searchable_blocks.append(("project_config", "config.yaml", "\n".join(f"{k}: {v}" for k, v in config.items())))

        metric_terms = {
            "adapter_percent": ("adapter", "接头"),
            "q30_ratio": ("q30",),
            "mapping_rate_percent": ("mapping", "mapping rate", "比对率"),
            "unique_mapping_rate_percent": ("unique", "unique mapping", "唯一比对"),
            "duplicate_rate_percent": ("duplicate", "duplication", "重复率", "picard"),
            "mt_rate_percent": ("chrmt", "chrmt/pt", "mtratio", "organelle", "线粒体", "叶绿体", "质体"),
            "frip_ratio": ("frip", "reads in peaks"),
            "correlation": ("spearman", "correlation", "相关性"),
        }
        sources: dict[str, dict[str, Any]] = {}
        for metric_key, terms in metric_terms.items():
            matched: list[dict[str, str]] = []
            for source_type, source_file, text in searchable_blocks:
                lower_text = text.lower()
                if not any(term.lower() in lower_text for term in terms):
                    continue
                line = next(
                    (raw.strip() for raw in text.splitlines() if any(term.lower() in raw.lower() for term in terms)),
                    text.strip()[:240],
                )
                matched.append({"source_type": source_type, "source_file": source_file, "evidence": line[:240]})
            code_formula = (workflow_summary.get("code_formula_sources", {}) or {}).get(metric_key)
            formula_complete = cls.is_complete_metric_formula(metric_key, code_formula)
            if matched or code_formula:
                source_level = "project_verified" if formula_complete else "project_metric_mentioned"
                sources[metric_key] = {
                    "source_level": source_level,
                    "formula": code_formula.get("formula", "") if formula_complete else "",
                    "formula_candidate": code_formula.get("formula", "") if code_formula and not formula_complete else "",
                    "formula_source": "project_code" if formula_complete else "project_code_partial" if code_formula else "not_found_in_project_code",
                    "formula_source_file": code_formula.get("source_file", "") if code_formula else "",
                    "formula_source_line": code_formula.get("source_line", "") if code_formula else "",
                    "threshold_source": "professional_default_unverified",
                    "matched_sources": matched[:4],
                    "needs_verification": not formula_complete,
                    "confidence": 0.8 if formula_complete else 0.5 if code_formula else 0.45,
                }
            else:
                sources[metric_key] = {
                    "source_level": "not_found_in_project",
                    "formula": "",
                    "formula_source": "not_found_in_project_code",
                    "formula_source_file": "",
                    "formula_source_line": "",
                    "threshold_source": "not_found_in_project",
                    "matched_sources": [],
                    "needs_verification": True,
                    "confidence": 0.25,
                }
        return sources

    @staticmethod
    def is_complete_metric_formula(metric_key: str, code_formula: dict[str, Any] | None) -> bool:
        if not isinstance(code_formula, dict):
            return False
        formula = str(code_formula.get("formula") or "").strip().lower()
        if not formula:
            return False
        if metric_key == "mt_rate_percent":
            return "/" in formula and any(token in formula for token in ("mapped", "total", "alignment"))
        return True

    @staticmethod
    def first_present(config: dict[str, str], keys: tuple[str, ...]) -> dict[str, str]:
        return {
            key: str(config.get(key, "")).strip()
            for key in keys
            if str(config.get(key, "")).strip()
        }

    @classmethod
    def build_workflow_rule_sources(
        cls,
        *,
        workflow_summary: dict[str, Any],
        config: dict[str, str],
        config_file: str,
    ) -> dict[str, dict[str, Any]]:
        detected = workflow_summary.get("detected_parameters", {}) or {}
        sources: dict[str, dict[str, Any]] = {}

        def add_config_rule(rule_key: str, label: str, keys: tuple[str, ...]) -> None:
            values = cls.first_present(config, keys)
            if not values:
                return
            evidence = "; ".join(f"{key}={value}" for key, value in values.items())
            sources[rule_key] = {
                "label": label,
                "source_level": "project_verified",
                "source_type": "project_config",
                "source_file": config_file or "config.yaml",
                "value": evidence,
                "evidence": evidence,
                "needs_verification": False,
                "confidence": 0.82,
            }

        def add_workflow_rule(rule_key: str, label: str, parameter_key: str) -> None:
            evidence = str(detected.get(parameter_key, "")).strip()
            if not evidence:
                return
            existing = sources.get(rule_key)
            if existing:
                existing["source_type"] = "project_config+workflow"
                existing["evidence"] = f"{existing.get('evidence', '')}; workflow: {evidence}"[:500]
                existing["value"] = f"{existing.get('value', '')}; workflow: {evidence}"[:500]
                existing["confidence"] = 0.9
                return
            sources[rule_key] = {
                "label": label,
                "source_level": "project_verified",
                "source_type": "project_workflow",
                "source_file": parameter_key,
                "value": evidence,
                "evidence": evidence,
                "needs_verification": False,
                "confidence": 0.78,
            }

        add_config_rule("reference_config", "reference genome and species configuration", ("species", "genome", "reference", "effective_genome_size"))
        add_config_rule("dedup_policy", "duplicate removal policy", ("remove_duplicates",))
        add_config_rule("peak_calling_params", "peak calling parameters", ("peak_caller", "peak_calling", "macs3_qvalue", "TOP_PEAKS_NUM"))
        add_config_rule("organelle_handling", "mitochondrial/plastid chromosome handling", ("organelle_chroms",))
        add_config_rule("trimming_policy", "adapter trimming policy", ("adapter_type", "trimming_tool"))
        add_config_rule("sequencing_mode", "sequencing mode", ("Sequencing", "sequencing_mode", "assay", "project_type"))

        add_workflow_rule("aligner_config", "alignment tool and command rule", "aligner")
        add_workflow_rule("peak_calling_params", "peak calling parameters", "peak_caller")
        add_workflow_rule("dedup_policy", "duplicate handling rule", "duplicate_handling")
        add_workflow_rule("organelle_handling", "organelle filtering rule", "organelle_filter")
        add_workflow_rule("reference_config", "reference genome and species configuration", "reference_genome")
        add_workflow_rule("frip_data_source", "FRiP data source rule", "frip")

        return sources

    @staticmethod
    def html_to_text(html: str) -> str:
        parser = _HTMLTextExtractor()
        parser.feed(html)
        return parser.text()

    @classmethod
    def extract_html_report_sections(cls, html: str) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        section_matches = list(re.finditer(r"<section\b[^>]*>(.*?)</section>", html, flags=re.IGNORECASE | re.DOTALL))
        if not section_matches:
            text = cls.html_to_text(html)
            return [{"title": "完整报告", "text": text[:80000]}] if text else []

        for match in section_matches:
            section_html = match.group(1)
            text = cls.html_to_text(section_html)
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if not lines:
                continue
            title = lines[0]
            body = "\n".join(lines[1:]).strip()
            if not body:
                body = title
            sections.append({"title": title[:120], "text": body[:10000]})
            if len(sections) >= 24:
                break
        return sections

    @staticmethod
    def format_report_sections(sections: list[dict[str, str]], max_chars: int = 30000) -> str:
        blocks: list[str] = []
        used = 0
        for index, section in enumerate(sections, start=1):
            title = section.get("title", f"Section {index}")
            text = section.get("text", "")
            block = f"## {index}. {title}\n{text}".strip()
            if not block:
                continue
            remaining = max_chars - used
            if remaining <= 0:
                break
            if len(block) > remaining:
                block = block[:remaining]
            blocks.append(block)
            used += len(block)
        return "\n\n".join(blocks)

    @classmethod
    def find_project_html_report(cls, root: Path, report_roots: list[Path], include_body: bool = True) -> dict[str, Any]:
        candidates: list[Path] = []
        preferred_names = [
            "CUTTag_report.html", "CUTTag_report.htm", "report.html", "report.htm",
            f"{root.name}.html", f"{root.name}.htm",
        ]
        for report_root in report_roots:
            candidates.extend(report_root / name for name in preferred_names)
            candidates.extend((report_root / "report" / name) for name in preferred_names)
        candidates.extend(root / name for name in preferred_names)
        for path in find_files(root, ["cuttag_report.html", "report.html", ".html"], limit=10):
            if path not in candidates:
                candidates.append(path)

        seen: set[Path] = set()
        for path in candidates:
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError):
                resolved = path
            if resolved in seen or not path.exists() or not path.is_file():
                continue
            seen.add(resolved)
            if not include_body:
                return {
                    "file": cls.relative_path(root, path),
                    "text_excerpt": "",
                    "sections": [],
                    "section_text": "",
                    "source": "project_html_report",
                }
            try:
                html = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            body_index = html.lower().find("<body")
            if body_index >= 0:
                html = html[body_index:]
            text = cls.html_to_text(html)
            sections = cls.extract_html_report_sections(html)
            section_text = cls.format_report_sections(sections)
            if not text:
                continue
            return {
                "file": cls.relative_path(root, path),
                "text_excerpt": text[:80000],
                "sections": sections,
                "section_text": section_text,
                "source": "project_html_report",
            }
        return {}

    @classmethod
    def build_project_context(cls, root: Path, include_html_body: bool = True) -> dict[str, Any]:
        report_roots = list_report_roots(root)
        samplelist_files = [path for path in (root / "samplelist", root / "samplelist.txt") if path.exists()]
        for path in find_files(root, ["samplelist"], limit=3):
            if path not in samplelist_files:
                samplelist_files.append(path)
        config_files = [path for path in (root / "config.yaml", root / "config.yml") if path.exists()]
        for path in find_files(root, ["config.yaml", "config.yml"], limit=3):
            if path not in config_files:
                config_files.append(path)
        logger.info("build_project_context root=%s config_files=%s", root, [str(p) for p in config_files])

        samples: list[dict[str, Any]] = []
        samplelist_path = ""
        if samplelist_files:
            samplelist_path = cls.relative_path(root, samplelist_files[0])
            try:
                samples = cls.parse_samplelist(samplelist_files[0])
            except OSError:
                samples = []

        config: dict[str, str] = {}
        config_path = ""
        if config_files:
            config_path = cls.relative_path(root, config_files[0])
            try:
                config = cls.parse_config_summary(config_files[0])
            except OSError:
                config = {}
        workflow_summary = cls.build_workflow_summary(root, project_config=config)
        metric_guides = cls.read_metric_guides(root, report_roots)
        workflow_rule_sources = cls.build_workflow_rule_sources(
            workflow_summary=workflow_summary,
            config=config,
            config_file=config_path,
        )
        sample_roles = cls.build_sample_roles(samples)
        experiment_design = experiment_design_service.build(samples, config=config, sample_roles=sample_roles)
        evidence_catalog = evidence_catalog_service.build(root)

        return {
            "samplelist_file": samplelist_path,
            "samples": samples,
            "sample_roles": sample_roles,
            "sample_roles_deprecated": True,
            "experiment_design": experiment_design,
            "evidence_catalog": evidence_catalog,
            "evidence_catalog_summary": evidence_catalog_service.summary_for_context(evidence_catalog),
            "metric_schema": metric_schema_service.export_schema(),
            "knowledge_base_contract": {
                "version": "project-analysis-kb-v1",
                "stores": [
                    "metric definitions, units, formulas, numerators, denominators, and physical ranges",
                    "assay and target-class distinctions",
                    "versioned literature or guideline references",
                    "project SOP and workflow rules",
                ],
                "reference_range_policy": "source-attributed and versioned; never promoted to a project threshold without project evidence",
                "diagnostic_procedure_policy": "stored in metadata-filtered Skill decision cards, not long tutorial prompts",
            },
            "sample_count": len(samples),
            "config_file": config_path,
            "config": config,
            "workflow_summary": workflow_summary,
            "workflow_rule_sources": workflow_rule_sources,
            "metric_rule_sources": cls.build_metric_rule_sources(
                metric_guides=metric_guides,
                workflow_summary=workflow_summary,
                config=config,
            ),
            "report_roots": [cls.relative_path(root, path) for path in report_roots],
            "html_report": cls.find_project_html_report(root, report_roots, include_body=include_html_body),
            "metric_guides": metric_guides,
            "metric_glossary": cls.metric_glossary(config),
        }

    @staticmethod
    def build_project_version(root: Path, project_context: dict[str, Any]) -> str:
        import hashlib
        digest = hashlib.sha1()
        digest.update(str(root).encode("utf-8", errors="ignore"))
        for record in ((project_context.get("evidence_catalog") or {}).get("files", []) or []):
            if not isinstance(record, dict):
                continue
            digest.update(str(record.get("path") or "").encode("utf-8", errors="ignore"))
            digest.update(str(record.get("size_bytes") or 0).encode("utf-8", errors="ignore"))
            digest.update(str(record.get("mtime_ns") or 0).encode("utf-8", errors="ignore"))
        return f"project-v1:{digest.hexdigest()[:16]}"


project_context_builder_service = ProjectContextBuilderService()
