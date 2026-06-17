from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from multi_agent.backed.app.infrastructure.tools.local.project_reader import resolve_project_root
from multi_agent.backed.app.services.project_context_intent_service import project_context_intent_service
from multi_agent.backed.app.services.project_session_state_service import project_session_state_service


@dataclass(frozen=True)
class ProjectCandidate:
    project_id: str
    project_root: str
    sample_names: tuple[str, ...]


class ProjectLocatorService:
    MIN_DIRECT_MATCH_SCORE = 120
    MIN_CONFIDENT_SCORE = 100
    MIN_SCORE_GAP = 30
    DEFAULT_BASE_DIRS = (
        Path(r"Y:\Result"),
        Path(r"Y:\Snakemake_Sop"),
    )
    MAX_PROJECT_SEARCH_DEPTH = 2
    GENERIC_TECH_TERMS = {
        "cuttag",
        "cut_tag",
        "cut-tag",
        "cut&tag",
        "cut",
        "tag",
        "atac",
        "chip",
        "chipseq",
    }
    PROJECT_ANALYSIS_TERMS = (
        "项目",
        "数据",
        "分析",
        "报告",
        "结果",
        "质控",
        "异常",
        "排查",
        "frip",
        "peak",
        "readsqc",
        "alignmentqc",
    )

    def _get_base_dirs(self) -> list[Path]:
        env_value = os.getenv("PROJECT_BASE_DIRS", "").strip()
        paths: list[Path] = []
        if env_value:
            for chunk in env_value.split(";"):
                chunk = chunk.strip()
                if chunk:
                    paths.append(Path(chunk))
        cwd = Path.cwd()
        paths.extend([cwd, cwd.parent])
        paths.append(Path(__file__).resolve().parents[5])
        paths.extend(self.DEFAULT_BASE_DIRS)
        unique: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError):
                resolved = path
            try:
                exists = resolved.exists()
            except (OSError, PermissionError):
                exists = False
            if resolved in seen or not exists:
                continue
            seen.add(resolved)
            unique.append(resolved)
        return unique

    def _project_search_depth(self) -> int:
        raw = os.getenv("PROJECT_SEARCH_DEPTH", "").strip()
        if not raw:
            return self.MAX_PROJECT_SEARCH_DEPTH
        try:
            return max(1, min(5, int(raw)))
        except ValueError:
            return self.MAX_PROJECT_SEARCH_DEPTH

    def _iter_project_dirs(self, base_dir: Path):
        pending: list[tuple[Path, int]] = [(base_dir, 0)]
        seen: set[Path] = set()
        max_depth = self._project_search_depth()
        while pending:
            current, depth = pending.pop(0)
            try:
                resolved_current = current.resolve()
            except (OSError, RuntimeError):
                resolved_current = current
            if resolved_current in seen:
                continue
            seen.add(resolved_current)
            if depth >= max_depth:
                continue
            try:
                children = sorted(current.iterdir(), key=lambda item: item.name.lower())
            except (OSError, PermissionError):
                continue
            for child in children:
                if not child.is_dir():
                    continue
                yield child
                pending.append((child, depth + 1))

    def _extract_sample_names(self, project_root: Path) -> tuple[str, ...]:
        samplelist = project_root / "samplelist"
        if not samplelist.exists():
            return ()
        lines = samplelist.read_text(encoding="utf-8", errors="ignore").splitlines()
        sample_names: list[str] = []
        for line in lines:
            parts = [part.strip() for part in line.split("\t") if part.strip()]
            if parts:
                sample_names.append(parts[0])
        return tuple(sample_names)

    @staticmethod
    def _looks_like_project_dir(path: Path) -> bool:
        markers = (
            path / "samplelist",
            path / "result",
            path / f"{path.name}_result",
            path / "Snakefile",
            path / "config.yaml",
        )
        return any(marker.exists() for marker in markers)

    @staticmethod
    def _extract_project_id_candidates(question: str, explicit_project_id: str | None = None) -> list[str]:
        candidates: list[str] = []
        if explicit_project_id:
            candidates.append(explicit_project_id.strip())
        text = question or ""
        patterns = (
            r"([A-Za-z0-9][A-Za-z0-9._-]{2,})\s*(?:这个|该)?项目",
            r"项目\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9._-]{2,})",
            r"\b([A-Za-z0-9][A-Za-z0-9._-]{2,})\b",
        )
        for pattern in patterns:
            for match in re.findall(pattern, text, flags=re.IGNORECASE):
                value = (match or "").strip().strip(".,;:()[]{}")
                if value and value not in candidates:
                    candidates.append(value)
        return candidates

    def _find_exact_project_candidate(self, project_id: str) -> ProjectCandidate | None:
        target = (project_id or "").strip().lower()
        if not target:
            return None
        for base_dir in self._get_base_dirs():
            direct = base_dir / project_id
            if direct.exists() and direct.is_dir():
                resolved = direct.resolve()
                return ProjectCandidate(
                    project_id=direct.name,
                    project_root=str(resolved),
                    sample_names=self._extract_sample_names(resolved),
                )
            for child in self._iter_project_dirs(base_dir):
                if child.name.lower() != target:
                    continue
                if not self._looks_like_project_dir(child):
                    continue
                resolved = child.resolve()
                return ProjectCandidate(
                    project_id=child.name,
                    project_root=str(resolved),
                    sample_names=self._extract_sample_names(resolved),
                )
        try:
            resolved = resolve_project_root(project_id)
        except Exception:
            return None
        return ProjectCandidate(
            project_id=resolved.name,
            project_root=str(resolved),
            sample_names=self._extract_sample_names(resolved),
        )

    def resolve_project_by_id(self, project_id: str) -> dict[str, Any] | None:
        candidate = self._find_exact_project_candidate(project_id)
        if candidate is None:
            return None
        return {
            "project_id": candidate.project_id,
            "project_root": candidate.project_root,
            "sample_names": list(candidate.sample_names),
            "matched_by": "project_id",
            "confidence": 0.99,
        }

    def list_projects(self, limit: int = 200) -> list[ProjectCandidate]:
        projects: list[ProjectCandidate] = []
        seen: set[Path] = set()
        for base_dir in self._get_base_dirs():
            for path in self._iter_project_dirs(base_dir):
                if len(projects) >= limit:
                    return projects
                if not self._looks_like_project_dir(path):
                    continue
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                projects.append(
                    ProjectCandidate(
                        project_id=path.name,
                        project_root=str(resolved),
                        sample_names=self._extract_sample_names(resolved),
                    )
                )
        return projects

    @staticmethod
    def _tokenize_question(question: str, project_id: str | None = None) -> list[str]:
        normalized = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", " ", question or "")
        tokens = [token.strip() for token in normalized.split() if token.strip()]
        if project_id:
            tokens.insert(0, project_id.strip())
        return tokens

    @classmethod
    def _filter_project_terms(cls, terms: Iterable[str]) -> list[str]:
        filtered: list[str] = []
        for term in terms:
            normalized = term.lower().strip()
            if not normalized or normalized in cls.GENERIC_TECH_TERMS:
                continue
            filtered.append(term)
        return filtered

    @classmethod
    def _has_project_analysis_intent(cls, question: str, project_id: str | None = None) -> bool:
        if project_id:
            return True
        normalized = (question or "").lower()
        return any(term.lower() in normalized for term in cls.PROJECT_ANALYSIS_TERMS)

    @staticmethod
    def _candidate_entry(candidate: ProjectCandidate, score: int, matched_terms: list[str]) -> dict[str, Any]:
        return {
            "project_id": candidate.project_id,
            "project_root": candidate.project_root,
            "score": score,
            "matched_terms": matched_terms,
        }

    def _score_candidate(self, candidate: ProjectCandidate, terms: Iterable[str], question: str) -> tuple[int, list[str]]:
        score = 0
        matched_terms: list[str] = []
        normalized_question = question.lower()
        candidate_id = candidate.project_id.lower()
        if candidate_id in normalized_question:
            score += 100
            matched_terms.append(candidate.project_id)

        for term in terms:
            lowered = term.lower().strip()
            if not lowered:
                continue
            if lowered == candidate_id:
                score += 120
                matched_terms.append(term)
                continue
            if len(lowered) >= 4 and lowered in candidate_id:
                score += 50
                matched_terms.append(term)
            for sample in candidate.sample_names:
                sample_lower = sample.lower()
                if lowered == sample_lower:
                    score += 80
                    matched_terms.append(term)
                    break
                if len(lowered) >= 4 and lowered in sample_lower:
                    score += 40
                    matched_terms.append(term)
                    break
        deduped_terms = list(dict.fromkeys(matched_terms))
        return score, deduped_terms

    @classmethod
    def _build_confidence(cls, top_score: int, runner_up_score: int | None) -> float:
        gap = top_score - (runner_up_score or 0)
        raw_confidence = min(0.99, (top_score / 140.0) * 0.7 + (max(gap, 0) / 120.0) * 0.3)
        return round(max(0.0, raw_confidence), 3)

    @classmethod
    def _is_confident_match(cls, top_score: int, runner_up_score: int | None) -> bool:
        if top_score >= cls.MIN_DIRECT_MATCH_SCORE:
            return True
        if top_score < cls.MIN_CONFIDENT_SCORE:
            return False
        if runner_up_score is None:
            return True
        return (top_score - runner_up_score) >= cls.MIN_SCORE_GAP

    def identify_project(
        self,
        question: str,
        project_id: str | None,
        user_id: str,
        session_id: str,
    ) -> dict[str, object]:
        state = project_session_state_service.load_state(user_id, session_id)
        intent = project_context_intent_service.classify(question, state)

        if project_id:
            exact = self._find_exact_project_candidate(project_id)
            if exact is None:
                exact = next(
                    (
                        candidate
                        for candidate in self.list_projects()
                        if candidate.project_id.lower() == project_id.lower()
                    ),
                    None,
                )
            if exact is None:
                raise FileNotFoundError(f"Project root not found for explicit project_id={project_id}")
            return {
                "matched_by": "project_id",
                "project_id": exact.project_id,
                "project_root": exact.project_root,
                "sample_names": list(exact.sample_names),
                "candidates": [],
                "matched_terms": [project_id],
                "confidence": 0.99,
                "needs_confirmation": False,
            }

        if (
            state.get("project_context_locked")
            and state.get("active_project_id")
            and state.get("active_project_root")
            and intent not in {"switch_project", "bind_project", "clear_project_context"}
        ):
            return {
                "matched_by": "active_context",
                "project_id": state["active_project_id"],
                "project_root": state["active_project_root"],
                "sample_names": [],
                "candidates": [],
                "matched_terms": [],
                "confidence": 0.99,
                "needs_confirmation": False,
            }

        for candidate_id in self._extract_project_id_candidates(question):
            exact = self._find_exact_project_candidate(candidate_id)
            if exact is None:
                continue
            return {
                "matched_by": "project_id",
                "project_id": exact.project_id,
                "project_root": exact.project_root,
                "sample_names": list(exact.sample_names),
                "candidates": [],
                "matched_terms": [candidate_id],
                "confidence": 0.99,
                "needs_confirmation": False,
            }

        projects = self.list_projects()

        if not self._has_project_analysis_intent(question, project_id):
            raise FileNotFoundError("No project-analysis intent found in question")

        terms = self._filter_project_terms(self._tokenize_question(question, project_id))
        if not terms:
            raise FileNotFoundError("No specific project term found in question")
        scored: list[tuple[int, ProjectCandidate, list[str]]] = []
        for item in projects:
            score, matched_terms = self._score_candidate(item, terms, question)
            if score > 0:
                scored.append((score, item, matched_terms))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        if scored:
            top_score, top_item, top_terms = scored[0]
            runner_up_score = scored[1][0] if len(scored) > 1 else None
            candidates = [
                self._candidate_entry(candidate, score, matched_terms)
                for score, candidate, matched_terms in scored[:5]
            ]
            confident = self._is_confident_match(top_score, runner_up_score)
            return {
                "matched_by": "question",
                "project_id": top_item.project_id,
                "project_root": top_item.project_root,
                "sample_names": list(top_item.sample_names),
                "candidates": candidates,
                "score": top_score,
                "matched_terms": top_terms,
                "confidence": self._build_confidence(top_score, runner_up_score),
                "needs_confirmation": not confident,
            }

        fallback_project_id = state.get("active_project_id") or state.get("current_project_id")
        fallback_project_root = state.get("active_project_root") or state.get("current_project_root")
        if fallback_project_id and fallback_project_root:
            return {
                "matched_by": "session_memory",
                "project_id": fallback_project_id,
                "project_root": fallback_project_root,
                "sample_names": [],
                "candidates": [],
                "matched_terms": [],
                "confidence": 0.6,
                "needs_confirmation": False,
            }

        raise FileNotFoundError("Unable to identify project from question, project_id, or session memory")


project_locator_service = ProjectLocatorService()
