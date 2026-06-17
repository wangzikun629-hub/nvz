from __future__ import annotations

import os
import re
from pathlib import Path
from time import monotonic
from typing import Any


class BioSkillReferenceService:
    """Index generic bioinformatics skills and load selected skills on demand."""

    ENV_KEYS = ("BIOSKILLS_DIR", "BIOSKILLS_PATH")
    MAX_FULL_SKILL_CHARS = 600
    INDEX_REFRESH_SECONDS = 30.0
    DOMAIN_TOKENS = {
        "adapter",
        "alignment",
        "atac-seq",
        "bowtie2",
        "chip-seq",
        "clean reads",
        "correlation",
        "cut&tag",
        "cutadapt",
        "deeptools",
        "duplicate",
        "fastp",
        "fastqc",
        "frip",
        "heatmap",
        "mapping",
        "macs",
        "mitochondrial",
        "organelle",
        "peak",
        "picard",
        "q30",
        "quality control",
        "read qc",
        "samtools",
        "scatter",
        "snakemake",
        "workflow",
    }

    BUILTIN_SKILLS: tuple[dict[str, Any], ...] = (
        {
            "id": "read-qc",
            "title": "Reads QC and adapter review",
            "applies_to": ["adapter_percent", "q20_ratio", "q30_ratio", "clean_reads"],
            "intents": ["anomaly_investigation", "project_overview", "metric_explanation"],
            "keywords": ["adapter", "cutadapt", "fastp", "fastqc", "reads qc", "接头", "q30"],
            "evidence_hints": ["ReadsQC", "cutadapt", "fastp", "FastQC", "adapter", "Q30"],
            "guidance": (
                "优先区分 raw reads 的接头检出、trim 过程和 clean reads 的接头残留，"
                "并核对过滤后保留率及 Q20/Q30。"
            ),
        },
        {
            "id": "alignment-qc",
            "title": "Alignment and organelle review",
            "applies_to": [
                "mapping_rate_percent",
                "unique_mapping_rate_percent",
                "duplicate_rate_percent",
                "chrmt_pt_rate_percent",
                "mt_rate_percent",
            ],
            "intents": ["anomaly_investigation", "project_overview", "metric_explanation"],
            "keywords": [
                "alignment",
                "mapping",
                "unique",
                "duplicate",
                "chrmt",
                "organelle",
                "mitochondrial",
                "比对",
                "线粒体",
            ],
            "evidence_hints": ["AlignmentQC", "bowtie2", "samtools", "Picard", "organelle", "chrMT"],
            "guidance": (
                "联合检查 mapping、unique、duplicate、线粒体或叶绿体比例及参考基因组口径，"
                "避免由单一指标直接推断根因。"
            ),
        },
        {
            "id": "enrichment-qc",
            "title": "CUT&Tag/ChIP/ATAC enrichment review",
            "applies_to": ["frip", "frip_ratio", "peak_count", "correlation"],
            "intents": ["anomaly_investigation", "project_overview", "chart_request"],
            "keywords": ["frip", "peak", "macs", "deeptools", "correlation", "富集", "相关性"],
            "evidence_hints": ["FRiP", "peak", "narrowPeak", "MACS", "deeptools", "Correlation"],
            "guidance": (
                "联合核对 FRiP、peak 数量、样本角色、相关性和有效比对 reads，"
                "不得把不同处理组自动当作生物学重复。"
            ),
        },
        {
            "id": "workflow-provenance",
            "title": "Workflow and rule provenance review",
            "applies_to": ["overview", "all"],
            "intents": ["metric_explanation", "anomaly_investigation", "project_overview"],
            "keywords": ["script", "snakefile", "snakemake", "config", "readme", "sop", "公式", "阈值"],
            "evidence_hints": ["Snakefile", "config", "README", "SOP", "script", "rule"],
            "guidance": "涉及公式、阈值或流程口径时，优先读取项目脚本、README、SOP 或报告说明。",
        },
    )

    def __init__(self, skill_root: str | Path | None = None) -> None:
        self._configured_root = Path(skill_root).expanduser() if skill_root else None
        self._local_cache_key = ""
        self._local_cache: list[dict[str, Any]] = []
        self._skill_path_by_id: dict[str, Path] = {}
        self._last_index_check = 0.0

    def select_references(
        self,
        *,
        question: str,
        target_metrics: list[str] | tuple[str, ...] | None,
        intent: str,
        limit: int = 4,
        assay_override: str = "",
        target_class: str = "",
        species_scope: str = "",
        available_evidence: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> list[dict[str, Any]]:
        metrics = {str(item).strip().lower() for item in (target_metrics or []) if str(item).strip()}
        if not metrics:
            metrics.add("overview")
        normalized_question = self._normalize_text(question)
        query_tokens = self._query_tokens(normalized_question)
        candidates = list(self.BUILTIN_SKILLS) + self._load_local_skills()
        assay = self._normalize_assay(assay_override) or self._infer_query_assay(
            normalized_question
        )

        scored: list[tuple[int, dict[str, Any]]] = []
        for item in candidates:
            if not self._metadata_allows(
                item,
                assay,
                target_class=target_class,
                species_scope=species_scope,
                available_evidence=available_evidence,
            ):
                continue
            score = self._score(item, normalized_question, query_tokens, metrics, intent)
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("id") or "")))

        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _, item in scored:
            ref_id = str(item.get("id") or item.get("title") or "")
            if not ref_id or ref_id in seen:
                continue
            seen.add(ref_id)
            selected.append(self._public_reference(item))
            if len(selected) >= limit:
                break
        return selected

    def select_for_project(
        self,
        *,
        question: str,
        target_metrics: list[str] | tuple[str, ...] | None,
        intent: str,
        assay: str,
        target_class: str = "",
        species_scope: str = "",
        available_evidence: list[str] | tuple[str, ...] | set[str] | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Re-select skills after project assay and evidence availability are known."""

        return self.select_references(
            question=question,
            target_metrics=target_metrics,
            intent=intent,
            limit=limit,
            assay_override=assay,
            target_class=target_class,
            species_scope=species_scope,
            available_evidence=available_evidence,
        )

    def load_full_skills(
        self,
        references: list[dict[str, Any]],
        *,
        max_skills: int = 2,
        max_chars: int | None = None,
    ) -> list[dict[str, Any]]:
        """Load compact diagnostic decision cards for selected references."""

        self._load_local_skills()
        char_limit = max(
            300,
            min(self.MAX_FULL_SKILL_CHARS, int(max_chars or self.MAX_FULL_SKILL_CHARS)),
        )
        loaded: list[dict[str, Any]] = []
        for reference in references:
            ref_id = str(reference.get("id") or "")
            path = self._skill_path_by_id.get(ref_id)
            if path is None:
                builtin = next((item for item in self.BUILTIN_SKILLS if item.get("id") == ref_id), None)
                if builtin:
                    decision_card = str(
                        builtin.get("decision_card")
                        or builtin.get("guidance")
                        or ""
                    )[:char_limit]
                    loaded.append(
                        {
                            "id": ref_id,
                            "title": str(builtin.get("title") or ""),
                            "source": str(reference.get("source") or f"bioSkills:{ref_id}"),
                            "content": decision_card,
                            "decision_card": decision_card,
                            "metadata": dict(
                                builtin.get("metadata")
                                or {
                                    "assay": ["all"],
                                    "target_class": ["all"],
                                    "species_scope": ["all"],
                                    "required_evidence": list(
                                        builtin.get("evidence_hints") or []
                                    ),
                                    "contraindications": [],
                                }
                            ),
                            "truncated": False,
                        }
                    )
            else:
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                indexed = next(
                    (
                        item
                        for item in self._local_cache
                        if str(item.get("id") or "") == ref_id
                    ),
                    {},
                )
                decision_card = str(
                    indexed.get("decision_card")
                    or self._summarize_skill(content)
                )[:char_limit]
                loaded.append(
                    {
                        "id": ref_id,
                        "title": str(reference.get("title") or path.parent.name),
                        "source": str(reference.get("source") or path),
                        "content": decision_card,
                        "decision_card": decision_card,
                        "metadata": dict(indexed.get("metadata") or {}),
                        "truncated": len(decision_card) >= char_limit,
                    }
                )
            if len(loaded) >= max_skills:
                break
        return loaded

    def index_stats(self) -> dict[str, Any]:
        local_skills = self._load_local_skills()
        return {
            "root": str(self._resolve_root() or ""),
            "indexed_local_skills": len(local_skills),
            "builtin_skills": len(self.BUILTIN_SKILLS),
            "total_indexed_skills": len(local_skills) + len(self.BUILTIN_SKILLS),
            "full_skill_loading": "compact_decision_cards_only",
        }

    def evidence_hints(self, references: list[dict[str, Any]], limit: int = 20) -> list[str]:
        hints: list[str] = []
        for ref in references:
            for item in ref.get("evidence_hints", []) or []:
                text = re.sub(r"\s+", " ", str(item or "")).strip()
                if text and text not in hints:
                    hints.append(text)
        return hints[:limit]

    @classmethod
    def _score(
        cls,
        item: dict[str, Any],
        question: str,
        query_tokens: set[str],
        metrics: set[str],
        intent: str,
    ) -> int:
        score = 0
        applies_to = {str(value).lower() for value in item.get("applies_to", []) or []}
        if metrics.intersection(applies_to) or "all" in applies_to:
            score += 8
        elif "overview" in applies_to and "overview" in metrics:
            score += 5
        if intent and intent in (item.get("intents", []) or []):
            score += 3
        keywords = {cls._normalize_text(value) for value in item.get("keywords", []) or []}
        search_text = cls._normalize_text(item.get("_search_text") or "")
        score += sum(3 for token in keywords if token and token in question)
        score += min(8, len(query_tokens.intersection(keywords)) * 2)
        score += min(6, sum(1 for token in query_tokens if len(token) >= 4 and token in search_text))
        return score

    @staticmethod
    def _public_reference(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(item.get("id") or ""),
            "title": str(item.get("title") or ""),
            "source_scope": "general_bioskills_reference",
            "source": str(item.get("source") or f"bioSkills:{item.get('id', '')}"),
            "applies_to": list(item.get("applies_to", []) or [])[:8],
            "evidence_hints": list(item.get("evidence_hints", []) or [])[:10],
            "guidance": str(item.get("guidance") or "")[:500],
            "metadata": dict(
                item.get("metadata")
                or {
                    "assay": ["all"],
                    "target_class": ["all"],
                    "species_scope": ["all"],
                    "required_evidence": list(item.get("evidence_hints") or [])[:10],
                    "contraindications": [],
                    "metadata_source": "builtin_default",
                }
            ),
            "boundary": "仅用于规划排查路线，不能作为项目专属阈值、SOP、交付标准或合格性判断依据。",
        }

    def _resolve_root(self) -> Path | None:
        if self._configured_root:
            return self._configured_root
        root_text = next((os.getenv(key) for key in self.ENV_KEYS if os.getenv(key)), "")
        if root_text:
            return Path(root_text).expanduser()
        workspace_root = Path(__file__).resolve().parents[5]
        candidate = workspace_root / "bioSkills"
        return candidate if candidate.exists() else None

    def _load_local_skills(self) -> list[dict[str, Any]]:
        root = self._resolve_root()
        if root is None or not root.exists():
            return []
        now = monotonic()
        if self._local_cache and now - self._last_index_check < self.INDEX_REFRESH_SECONDS:
            return self._local_cache
        self._last_index_check = now
        try:
            skill_files = sorted(path for path in root.rglob("SKILL.md") if ".git" not in path.parts)
        except OSError:
            return []
        signature_parts = []
        for path in skill_files:
            try:
                stat = path.stat()
            except OSError:
                continue
            signature_parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        cache_key = f"{root.resolve()}|{len(skill_files)}|" + "|".join(signature_parts)
        if cache_key == self._local_cache_key:
            return self._local_cache

        skills: list[dict[str, Any]] = []
        paths: dict[str, Path] = {}
        for skill_file in skill_files:
            try:
                content = skill_file.read_text(encoding="utf-8", errors="replace")
                relative = str(skill_file.relative_to(root)).replace("\\", "/")
            except OSError:
                continue
            frontmatter = self._frontmatter(content)
            title = (
                str(frontmatter.get("name") or frontmatter.get("title") or "").strip()
                or self._extract_title(content)
                or skill_file.parent.name
            )
            description = str(frontmatter.get("description") or "").strip()
            searchable = self._normalize_text(f"{relative} {title} {description} {content[:6000]}")
            keywords = self._keywords_from_text(searchable)
            metadata = self._skill_metadata(
                frontmatter=frontmatter,
                searchable=searchable,
                relative=relative,
            )
            ref_id = f"local:{relative}"
            skills.append(
                {
                    "id": ref_id,
                    "title": title[:160],
                    "source": f"bioSkills:{relative}",
                    "applies_to": self._infer_applies_to(keywords),
                    "intents": ["metric_explanation", "anomaly_investigation", "project_overview", "chart_request"],
                    "keywords": sorted(keywords),
                    "evidence_hints": self._infer_evidence_hints(keywords),
                    "guidance": description[:500] or self._summarize_skill(content),
                    "decision_card": self._decision_card(content),
                    "metadata": metadata,
                    "_search_text": searchable,
                }
            )
            paths[ref_id] = skill_file

        self._local_cache_key = cache_key
        self._local_cache = skills
        self._skill_path_by_id = paths
        return skills

    @staticmethod
    def _frontmatter(content: str) -> dict[str, str]:
        if not content.startswith("---"):
            return {}
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}
        payload: dict[str, str] = {}
        for line in parts[1].splitlines():
            match = re.match(r"^\s*([A-Za-z0-9_-]+)\s*:\s*(.+?)\s*$", line)
            if match:
                payload[match.group(1).lower()] = match.group(2).strip().strip("'\"")
        return payload

    @classmethod
    def _skill_metadata(
        cls,
        *,
        frontmatter: dict[str, str],
        searchable: str,
        relative: str,
    ) -> dict[str, Any]:
        explicit_assay = cls._list_value(frontmatter.get("assay"))
        inferred_assay = cls._infer_skill_assays(f"{relative} {searchable}")
        return {
            "assay": explicit_assay or inferred_assay or ["all"],
            "target_class": cls._list_value(frontmatter.get("target_class")) or ["all"],
            "species_scope": cls._list_value(frontmatter.get("species_scope")) or ["all"],
            "required_evidence": cls._list_value(frontmatter.get("required_evidence")),
            "contraindications": cls._list_value(frontmatter.get("contraindications")),
            "metadata_source": "frontmatter" if explicit_assay else "legacy_inference",
        }

    @classmethod
    def _metadata_allows(
        cls,
        item: dict[str, Any],
        assay: str,
        *,
        target_class: str = "",
        species_scope: str = "",
        available_evidence: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> bool:
        if not assay:
            return True
        metadata = item.get("metadata") or {}
        assays = {cls._normalize_assay(value) for value in metadata.get("assay", []) or []}
        contraindications = {
            cls._normalize_assay(value)
            for value in metadata.get("contraindications", []) or []
        }
        if assay in contraindications:
            return False
        if assays and "all" not in assays and assay not in assays:
            return False
        normalized_target = cls._normalize_text(target_class)
        targets = {
            cls._normalize_text(value)
            for value in metadata.get("target_class", []) or []
        }
        if (
            normalized_target
            and targets
            and "all" not in targets
            and normalized_target not in targets
        ):
            return False
        normalized_species = cls._normalize_text(species_scope)
        species = {
            cls._normalize_text(value)
            for value in metadata.get("species_scope", []) or []
        }
        if (
            normalized_species
            and species
            and "all" not in species
            and normalized_species not in species
        ):
            return False
        if available_evidence is not None:
            available = {
                cls._normalize_evidence_token(value)
                for value in available_evidence
                if str(value).strip()
            }
            required = {
                cls._normalize_evidence_token(value)
                for value in metadata.get("required_evidence", []) or []
                if str(value).strip()
            }
            if required and not required.intersection(available):
                return False
        search_text = cls._normalize_text(
            item.get("_search_text") or item.get("title") or ""
        )
        if assay == "cuttag" and metadata.get("metadata_source") != "frontmatter":
            excluded = (
                "atac",
                "atac-seq",
                "atacseq",
                "scatac",
                "single-cell atac",
                "amplicon",
            )
            if any(token in search_text for token in excluded):
                return False
        return True

    @classmethod
    def _infer_query_assay(cls, question: str) -> str:
        normalized = cls._normalize_text(question)
        for token, assay in (
            ("cut&tag", "cuttag"),
            ("cuttag", "cuttag"),
            ("cut-and-tag", "cuttag"),
            ("cut&run", "cutrun"),
            ("cutrun", "cutrun"),
            ("chip-seq", "chipseq"),
            ("chipseq", "chipseq"),
            ("scatac", "scatacseq"),
            ("atac-seq", "atacseq"),
            ("atacseq", "atacseq"),
            ("rna-seq", "rnaseq"),
            ("rnaseq", "rnaseq"),
        ):
            if token in normalized:
                return assay
        return ""

    @classmethod
    def _infer_skill_assays(cls, text: str) -> list[str]:
        normalized = cls._normalize_text(text)
        assays: list[str] = []
        for token, assay in (
            ("cut&tag", "cuttag"),
            ("cuttag", "cuttag"),
            ("cut&run", "cutrun"),
            ("cutrun", "cutrun"),
            ("chip-seq", "chipseq"),
            ("chipseq", "chipseq"),
            ("scatac", "scatacseq"),
            ("atac-seq", "atacseq"),
            ("atacseq", "atacseq"),
            ("amplicon", "amplicon"),
            ("rna-seq", "rnaseq"),
            ("rnaseq", "rnaseq"),
        ):
            if token in normalized and assay not in assays:
                assays.append(assay)
        return assays

    @staticmethod
    def _normalize_assay(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

    @staticmethod
    def _list_value(value: Any) -> list[str]:
        text = str(value or "").strip()
        if not text:
            return []
        text = text.strip("[]")
        return [
            token.strip().strip("'\"")
            for token in re.split(r"[,;|]+", text)
            if token.strip().strip("'\"")
        ]

    @classmethod
    def _decision_card(cls, content: str) -> str:
        lines: list[str] = []
        capture = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith(
                ("## decision", "## diagnostic", "## procedure", "## workflow")
            ):
                capture = True
                continue
            if capture and stripped.startswith("## "):
                break
            if capture and stripped and not stripped.startswith("---"):
                lines.append(stripped)
            if len(" ".join(lines)) >= cls.MAX_FULL_SKILL_CHARS:
                break
        return (
            " ".join(lines)[: cls.MAX_FULL_SKILL_CHARS]
            if lines
            else cls._summarize_skill(content)[: cls.MAX_FULL_SKILL_CHARS]
        )

    @staticmethod
    def _extract_title(content: str) -> str:
        for line in content.splitlines():
            match = re.match(r"^#\s+(.+)$", line.strip())
            if match:
                return match.group(1).strip()[:160]
        return ""

    @classmethod
    def _keywords_from_text(cls, text: str) -> set[str]:
        keywords = {token for token in cls.DOMAIN_TOKENS if token in text}
        for token in re.findall(r"[a-z][a-z0-9&+_.-]{2,}", text):
            if token in cls.DOMAIN_TOKENS:
                keywords.add(token)
        return keywords

    @staticmethod
    def _query_tokens(text: str) -> set[str]:
        tokens = set(re.findall(r"[a-z][a-z0-9&+_.-]{2,}|[\u4e00-\u9fff]{2,8}", text))
        return {token for token in tokens if token}

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().lower()

    @classmethod
    def _normalize_evidence_token(cls, value: Any) -> str:
        token = re.sub(r"[^a-z0-9]+", "", cls._normalize_text(value))
        aliases = {
            "fripratio": "frip",
            "peakcount": "peak",
            "peaks": "peak",
            "mappingratepercent": "mapping",
            "uniquemappingratepercent": "uniquemapping",
            "spikeinuniquemappingratepercent": "spikeinunique",
            "spikeinmappedreads": "spikeinmapped",
            "spikeinscalingfactor": "scalingfactor",
            "samplecorrelation": "correlation",
        }
        return aliases.get(token, token)

    @staticmethod
    def _infer_applies_to(keywords: set[str]) -> list[str]:
        keyword_map = {
            "adapter": "adapter_percent",
            "q30": "q30_ratio",
            "mapping": "mapping_rate_percent",
            "alignment": "mapping_rate_percent",
            "duplicate": "duplicate_rate_percent",
            "picard": "duplicate_rate_percent",
            "mitochondrial": "mt_rate_percent",
            "organelle": "mt_rate_percent",
            "frip": "frip_ratio",
            "peak": "peak_count",
            "correlation": "correlation",
            "heatmap": "chart_data",
            "scatter": "chart_data",
            "snakemake": "overview",
            "workflow": "overview",
        }
        applies: list[str] = []
        for keyword, metric in keyword_map.items():
            if keyword in keywords and metric not in applies:
                applies.append(metric)
        return applies or ["overview"]

    @staticmethod
    def _infer_evidence_hints(keywords: set[str]) -> list[str]:
        hint_map = {
            "adapter": "adapter",
            "cutadapt": "cutadapt",
            "fastp": "fastp",
            "fastqc": "FastQC",
            "mapping": "AlignmentQC",
            "alignment": "AlignmentQC",
            "duplicate": "duplicate",
            "picard": "Picard",
            "mitochondrial": "mt_stat",
            "organelle": "organelle",
            "frip": "FRiP",
            "peak": "peak",
            "macs": "MACS",
            "deeptools": "deeptools",
            "correlation": "Correlation",
            "snakemake": "Snakefile",
            "workflow": "workflow",
        }
        return [hint for keyword, hint in hint_map.items() if keyword in keywords][:10]

    @staticmethod
    def _summarize_skill(content: str) -> str:
        lines: list[str] = []
        for line in content.splitlines():
            text = re.sub(r"\s+", " ", line.strip())
            if text and not text.startswith(("#", "---")):
                lines.append(text)
            if len(" ".join(lines)) >= 500:
                break
        return " ".join(lines)[:500]


bio_skill_reference_service = BioSkillReferenceService()
