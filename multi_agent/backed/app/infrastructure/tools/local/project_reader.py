from __future__ import annotations

import csv
import hashlib
import os
import posixpath
import stat
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Iterable, Mapping
from urllib.parse import quote, unquote, urlparse


@dataclass(frozen=True)
class ProjectFile:
    path: Path
    kind: str


@dataclass
class ProjectFileIndex:
    root: Path
    root_mtime_ns: int
    files: list[ProjectFile]


_PROJECT_FILE_INDEX_CACHE: dict[Path, ProjectFileIndex] = {}
_COMMON_EVIDENCE_FILES_CACHE: dict[Path, tuple[int, list[Path]]] = {}
_DEFAULT_PROJECT_BASE_DIRS: tuple[str, ...] = (
    r"Y:\Result",
    r"Y:\Snakemake_Sop",
    "sftp://wangzk@10.11.0.16:22/beegfs/Pipline_cloud/data_cloud/Result",
    "sftp://wangzk@10.11.0.16:22/beegfs/Pipline_cloud/data_cloud/Snakemake_Sop",
)
_DEFAULT_SOP_BASE_DIRS: tuple[str, ...] = (
    r"Y:\Snakemake_Sop",
    "sftp://wangzk@10.11.0.16:22/beegfs/Pipline_cloud/data_cloud/Snakemake_Sop",
)
_MAX_INDEXED_FILES = 1200
_MAX_COMMON_EVIDENCE_FILES = 600
_MAX_INDEXED_DIRS = 220
_MAX_COMMON_EVIDENCE_DIRS = 120
_MAX_INTERNAL_WORKFLOW_DIRS = 80
_MAX_INTERNAL_WORKFLOW_FILES = 400
_MAX_LOCAL_SCAN_SECONDS = 4.0
_MAX_PROJECT_SEARCH_DEPTH = 2
_SKIP_DIR_NAMES = {
    ".business_agent",
    "__pycache__",
    ".git",
    ".pytest_cache",
    "bw",
    "genedep",
    "image",
    "images",
    "tmp",
    "tssheatmap",
    "unmap",
    "homerresults",
    "knownresults",
}
_SKIP_DIR_TOKENS = (
    "pathwayhtml",
    "gene_map",
    "motifyhtml",
)
_INDEXED_SUFFIXES = {
    "",
    ".csv",
    ".htm",
    ".html",
    ".log",
    ".md",
    ".tab",
    ".tsv",
    ".txt",
    ".xls",
    ".yaml",
    ".yml",
}
_INTERNAL_WORKFLOW_SUFFIXES = {
    ".py",
    ".r",
    ".R",
    ".rmd",
    ".Rmd",
    ".sh",
    ".bash",
    ".awk",
    ".sed",
    ".pl",
    ".smk",
    ".rule",
    ".rules",
    ".conf",
    ".ini",
    ".json",
    ".pbs",
    ".slurm",
    ".sbatch",
    ".yaml",
    ".yml",
}
_INTERNAL_WORKFLOW_NAMES = {
    "snakefile",
}
_SOP_CACHE_RELATIVE_FILES = (
    "config.yaml",
    "config.yml",
    "Snakefile",
    "main.sh",
    "run.sh",
    "workflow.py",
    "pipeline.py",
    "rules/1.QC.smk",
    "CP_rule/1.QC.smk",
    "Filter/cutadapt_stat.py",
    "Filter/m_cutadapt.py",
    "Align/align_stat.py",
    "Align/merge_all_stat.py",
    "rules/3.Align.smk",
    "CP_rule/3.Align.smk",
    "rules/6.macs3.smk",
    "CP_rule/6.macs3.smk",
)
_REMOTE_CACHE_SUFFIXES = _INDEXED_SUFFIXES | _INTERNAL_WORKFLOW_SUFFIXES
_PRIORITY_DIR_TOKENS = (
    "filter",
    "readsqc",
    "rules",
    "cp_rule",
    "pipline",
    "cuttag-h3_result",
    "_result",
    "alignmentqc",
    "correlation",
    "peakcalling",
    "5.1peakstat",
    "5.3peakanno",
    "5.4enrichment",
    "diffanalysis",
    "motifyanalysis",
    "7.motif",
    "spikein",
    "deeptools",
    "alignment",
    "filter",
    "motif",
)
_WORKFLOW_ALIASES = {
    "cuttag": "CUTTag",
    "cut&tag": "CUTTag",
    "cutrun": "CUTTag",
    "cut_run": "CUTTag",
    "atac": "ATAC",
    "rna-seq": "RNA-seq",
    "rnaseq": "RNA-seq",
    "rna_seq": "RNA-seq",
    "hi-c": "Hi-C_V2",
    "hic": "Hi-C_V2",
    "primer": "Primer",
    "foodie": "FOODIE",
}


@dataclass(frozen=True)
class SftpLocation:
    host: str
    port: int
    username: str | None
    password: str | None
    remote_path: str
    url: str


def _get_config_value(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is not None:
        return value.strip()

    env_path = Path(__file__).resolve().parents[3] / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return default

    prefix = f"{name}="
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or not stripped.startswith(prefix):
            continue
        return stripped[len(prefix):].strip().strip('"').strip("'")
    return default


def _get_env_project_base_dirs() -> list[str]:
    env_value = _get_config_value("PROJECT_BASE_DIRS")
    if not env_value:
        return []
    return [chunk.strip() for chunk in env_value.split(";") if chunk.strip()]


def _get_env_sop_base_dirs() -> list[str]:
    env_value = _get_config_value("PROJECT_SOP_BASE_DIRS")
    if not env_value:
        return []
    return [chunk.strip() for chunk in env_value.split(";") if chunk.strip()]


def _get_project_base_dirs() -> list[str]:
    paths: list[str] = []
    paths.extend(_get_env_project_base_dirs())

    cwd = Path.cwd()
    paths.extend([str(cwd), str(cwd.parent)])

    repo_root = Path(__file__).resolve().parents[6]
    paths.append(str(repo_root))
    paths.extend(_DEFAULT_PROJECT_BASE_DIRS)

    unique: list[str] = []
    seen: set[str] = set()
    for path in paths:
        key = path.rstrip("\\/")
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _validate_project_id(project_id: str) -> str:
    value = str(project_id or "").strip()
    if (
        not value
        or len(value) > 200
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("Invalid project_id")
    return value


def _is_within_path(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _validate_explicit_local_root(project_root: str) -> None:
    enforce = _get_config_value("PROJECT_ENFORCE_ROOT_ALLOWLIST").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    enforce = enforce or _get_config_value("APP_ENV").lower() == "production"
    if not enforce:
        return

    allowed_roots = [
        Path(value).expanduser()
        for value in _get_env_project_base_dirs()
        if value and not _is_sftp_url(value)
    ]
    for name in ("PROJECT_SFTP_CACHE_DIR", "PROJECT_WORKSPACE_DIR"):
        configured = _get_config_value(name)
        if configured:
            allowed_roots.append(Path(configured).expanduser())
    candidate = Path(project_root).expanduser()
    if not allowed_roots or not any(_is_within_path(candidate, root) for root in allowed_roots):
        raise PermissionError("Explicit project_root is outside configured project directories")


def _get_sop_base_dirs() -> list[str]:
    paths: list[str] = []
    paths.extend(_get_env_sop_base_dirs())
    paths.extend(_DEFAULT_SOP_BASE_DIRS)
    for path in _get_env_project_base_dirs():
        if "snakemake_sop" in path.lower():
            paths.append(path)

    unique: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        key = raw_path.rstrip("\\/").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        if _is_sftp_url(raw_path):
            unique.append(raw_path)
            continue
        path = Path(raw_path)
        try:
            if path.exists() and path.is_dir():
                unique.append(str(path))
        except (OSError, PermissionError):
            continue
    return unique


def _infer_workflow_names(project_root: Path, project_config: Mapping[str, object] | None = None) -> list[str]:
    values: list[str] = []
    config = project_config or {}
    for key in ("Sequencing", "sequencing", "pipeline", "workflow", "assay", "project_type"):
        value = str(config.get(key) or "").strip()
        if value:
            values.append(value)
    values.extend(str(part) for part in project_root.parts)

    inferred: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.lower().replace("\\", "/")
        for token, canonical in _WORKFLOW_ALIASES.items():
            if token in normalized and canonical.lower() not in seen:
                seen.add(canonical.lower())
                inferred.append(canonical)
    return inferred


def _sop_workflow_roots(project_root: Path, project_config: Mapping[str, object] | None = None) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()

    def add_root(path: Path) -> None:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            resolved = path
        if resolved in seen:
            return
        try:
            if not path.exists() or not path.is_dir():
                return
        except (OSError, PermissionError):
            return
        seen.add(resolved)
        roots.append(path)

    workflow_names = _infer_workflow_names(project_root, project_config)
    for raw_sop_base in _get_sop_base_dirs():
        if _is_sftp_url(raw_sop_base):
            for workflow_name in workflow_names:
                for location in _remote_sop_workflow_locations(raw_sop_base, workflow_name):
                    cached_root = _sftp_sop_cache_root(location, workflow_name)
                    add_root(cached_root)
                    if _sftp_offline() or _sop_cache_ready(cached_root):
                        continue
                    try:
                        mirrored = _mirror_sftp_workflow(
                            location,
                            local_project_root=cached_root,
                        )
                    except Exception:
                        continue
                    if mirrored is not None:
                        add_root(mirrored)
                        break
            continue

        sop_base = Path(raw_sop_base)
        for workflow_name in workflow_names:
            workflow_root = sop_base / workflow_name
            before_count = len(roots)
            if workflow_name == "ATAC":
                add_root(workflow_root / "snakemake_image" / "ATAC_pipline")
                add_root(workflow_root / "snakemake_image" / "pipline")
            else:
                add_root(workflow_root / "snakemake_image" / "pipline")
            if len(roots) == before_count:
                add_root(workflow_root / "snakemake_image")
                add_root(workflow_root)
    return roots


def _project_search_depth() -> int:
    raw = _get_config_value("PROJECT_SEARCH_DEPTH")
    if not raw:
        return _MAX_PROJECT_SEARCH_DEPTH
    try:
        return max(0, min(5, int(raw)))
    except ValueError:
        return _MAX_PROJECT_SEARCH_DEPTH


def _iter_local_project_candidates(base_dir: Path, project_id: str):
    target = (project_id or "").strip().lower()
    if not target:
        return

    direct = base_dir / project_id
    yield direct

    pending: list[tuple[Path, int]] = [(base_dir, 0)]
    seen: set[Path] = set()
    max_depth = _project_search_depth()
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
            if _should_skip_dir(child):
                continue
            if child.name.lower() == target:
                yield child
            pending.append((child, depth + 1))


def _is_sftp_url(value: str) -> bool:
    return value.lower().startswith("sftp://")


def _parse_sftp_url(value: str) -> SftpLocation:
    parsed = urlparse(value)
    if parsed.scheme.lower() != "sftp" or not parsed.hostname:
        raise ValueError(f"Invalid SFTP URL: {value}")
    username = unquote(parsed.username) if parsed.username else _get_config_value("PROJECT_SFTP_USER") or None
    password = unquote(parsed.password) if parsed.password else _get_config_value("PROJECT_SFTP_PASSWORD") or None
    remote_path = unquote(parsed.path or "/")
    return SftpLocation(
        host=parsed.hostname,
        port=parsed.port or int(_get_config_value("PROJECT_SFTP_PORT", "22")),
        username=username,
        password=password,
        remote_path=remote_path.rstrip("/") or "/",
        url=value,
    )


def _build_sftp_url(location: SftpLocation, remote_path: str) -> str:
    user = f"{quote(location.username)}@" if location.username else ""
    return f"sftp://{user}{location.host}:{location.port}{quote(remote_path, safe='/')}"


def _sftp_offline() -> bool:
    return _get_config_value("PROJECT_SFTP_OFFLINE").lower() in {"1", "true", "yes", "on"}


def _remote_sop_workflow_locations(sop_base_url: str, workflow_name: str) -> list[SftpLocation]:
    base = _parse_sftp_url(sop_base_url)
    workflow_root = posixpath.join(base.remote_path, workflow_name)
    relative_roots = (
        ("snakemake_image", "ATAC_pipline"),
        ("snakemake_image", "pipline"),
        ("snakemake_image",),
        (),
    ) if workflow_name == "ATAC" else (
        ("snakemake_image", "pipline"),
        ("snakemake_image",),
        (),
    )
    return [
        SftpLocation(
            host=base.host,
            port=base.port,
            username=base.username,
            password=base.password,
            remote_path=posixpath.join(workflow_root, *parts),
            url=_build_sftp_url(base, posixpath.join(workflow_root, *parts)),
        )
        for parts in relative_roots
    ]


def _open_sftp(location: SftpLocation):
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("SFTP support requires the 'paramiko' package. Install dependencies first.") from exc

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    key_filename = _get_config_value("PROJECT_SFTP_KEY_FILE") or None
    client.connect(
        hostname=location.host,
        port=location.port,
        username=location.username,
        password=location.password,
        key_filename=key_filename,
        look_for_keys=True,
        allow_agent=True,
        timeout=15,
        banner_timeout=15,
        auth_timeout=15,
    )
    return client, client.open_sftp()


def _remote_is_dir(sftp, remote_path: str) -> bool:
    try:
        return stat.S_ISDIR(sftp.stat(remote_path).st_mode)
    except OSError:
        return False


def _iter_remote_project_paths(location: SftpLocation, project_id: str) -> list[str]:
    target = (project_id or "").strip().lower()
    if not target:
        return []

    client = None
    matches: list[str] = []
    seen_dirs: set[str] = set()
    try:
        client, sftp = _open_sftp(location)
        direct = posixpath.join(location.remote_path, project_id)
        if _remote_is_dir(sftp, direct):
            matches.append(direct)

        pending: list[tuple[str, int]] = [(location.remote_path, 0)]
        max_depth = _project_search_depth()
        while pending:
            current, depth = pending.pop(0)
            if current in seen_dirs:
                continue
            seen_dirs.add(current)
            if depth >= max_depth:
                continue
            try:
                children = sorted(sftp.listdir_attr(current), key=lambda item: item.filename.lower())
            except OSError:
                continue
            for child in children:
                remote_child = posixpath.join(current, child.filename)
                # Follow symlinks: listdir_attr returns lstat() so symlinks to
                # directories appear as S_ISLNK, not S_ISDIR. Use sftp.stat()
                # (which resolves symlinks) to confirm the entry is a directory.
                is_dir = stat.S_ISDIR(child.st_mode) or (
                    stat.S_ISLNK(child.st_mode) and _remote_is_dir(sftp, remote_child)
                )
                if not is_dir:
                    continue
                if _should_skip_remote_dir(remote_child):
                    continue
                if child.filename.lower() == target and remote_child not in matches:
                    matches.append(remote_child)
                pending.append((remote_child, depth + 1))
        return matches
    finally:
        if client is not None:
            client.close()


def _sftp_cache_base() -> Path:
    configured = _get_config_value("PROJECT_SFTP_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[3] / ".project_sftp_cache"


def _sftp_cache_root(location: SftpLocation, project_id: str) -> Path:
    digest = hashlib.sha1(f"{location.host}:{location.port}:{location.remote_path}".encode("utf-8")).hexdigest()[:12]
    return _sftp_cache_base() / digest / project_id


def _sftp_sop_cache_root(location: SftpLocation, workflow_name: str) -> Path:
    digest = hashlib.sha1(f"{location.host}:{location.port}:{location.remote_path}".encode("utf-8")).hexdigest()[:12]
    return _sftp_cache_base() / "sop" / digest / "Snakemake_Sop" / workflow_name


def _sop_cache_ready(cache_root: Path) -> bool:
    return (cache_root / ".sftp-cache-complete").exists()


def _iter_cached_project_roots(project_id: str) -> Iterable[Path]:
    cache_base = _sftp_cache_base()
    direct = cache_base / project_id
    yield direct
    try:
        namespaces = sorted(cache_base.iterdir(), key=lambda item: item.name.lower())
    except (OSError, PermissionError):
        return
    for namespace in namespaces:
        try:
            if namespace.is_dir():
                yield namespace / project_id
        except (OSError, PermissionError):
            continue


def _remote_report_roots(sftp, remote_project_root: str, project_id: str) -> list[str]:
    candidates = [
        posixpath.join(remote_project_root, "final_report"),
        posixpath.join(remote_project_root, project_id),
        posixpath.join(remote_project_root, f"{project_id}_result"),
        posixpath.join(remote_project_root, "result", f"{project_id}_result"),
    ]
    try:
        for item in sftp.listdir_attr(remote_project_root):
            if not stat.S_ISDIR(item.st_mode):
                continue
            child = posixpath.join(remote_project_root, item.filename)
            if item.filename in {"final_report", project_id, f"{project_id}_result"}:
                candidates.append(child)
                continue
            try:
                child_names = {entry.filename for entry in sftp.listdir_attr(child)}
            except OSError:
                continue
            if {"1.ReadsQC", "2.AlignmentQC"} & child_names or {"5.Peakcalling", "7.Motif"} & child_names:
                candidates.append(child)
    except OSError:
        pass

    unique: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        normalized = path.rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _remote_report_evidence_dirs(remote_project_root: str, report_root: str) -> list[str]:
    return [
        remote_project_root,
        posixpath.join(remote_project_root, "result", "Spikein"),
        posixpath.join(remote_project_root, "result", "deeptools", "Correlation"),
        posixpath.join(report_root, "1.ReadsQC"),
        posixpath.join(report_root, "2.AlignmentQC", "2.1AlignmentStat"),
        posixpath.join(report_root, "3.Correlation"),
        posixpath.join(report_root, "4.TSSHeatmap"),
        posixpath.join(report_root, "5.Peakcalling", "5.1PeakStat"),
        posixpath.join(report_root, "5.Peakcalling", "5.3PeakAnno"),
        posixpath.join(report_root, "5.Peakcalling", "5.4Enrichment"),
        posixpath.join(report_root, "5.Peakcalling", "5.6peakFrip"),
        posixpath.join(report_root, "6.forIGV"),
        posixpath.join(report_root, "7.Motif"),
        posixpath.join(report_root, "7.DiffAnalysis", "7.1DiffPeak"),
        posixpath.join(report_root, "7.DiffAnalysis", "7.3DiffGOEnrichment"),
        posixpath.join(report_root, "7.DiffAnalysis", "7.4DiffPathwayEnrichment"),
        posixpath.join(report_root, "8.MotifyAnalysis", "8.1Motify"),
    ]


def _should_skip_remote_dir(remote_path: str) -> bool:
    name = posixpath.basename(remote_path).lower()
    normalized = remote_path.lower().replace("/", "\\")
    return (
        name in _SKIP_DIR_NAMES
        or any(token in normalized for token in _SKIP_DIR_TOKENS)
        or "\\report\\image" in normalized
    )


def _sort_remote_child(remote_path: str) -> tuple[int, str]:
    normalized = remote_path.lower().replace("/", "\\")
    priority = next(
        (index for index, token in enumerate(_PRIORITY_DIR_TOKENS) if token in normalized),
        len(_PRIORITY_DIR_TOKENS),
    )
    return (priority, posixpath.basename(remote_path).lower())


def _download_remote_file(sftp, remote_file: str, remote_project_root: str, local_project_root: Path) -> Path:
    relative = posixpath.relpath(remote_file, remote_project_root)
    local_file = local_project_root.joinpath(*relative.split("/"))
    local_file.parent.mkdir(parents=True, exist_ok=True)
    remote_stat = sftp.stat(remote_file)
    if local_file.exists() and local_file.stat().st_size == remote_stat.st_size:
        return local_file
    sftp.get(remote_file, str(local_file))
    return local_file


def _mirror_sftp_workflow(
    location: SftpLocation,
    *,
    local_project_root: Path,
) -> Path | None:
    client = None
    downloaded = 0
    try:
        client, sftp = _open_sftp(location)
        if not _remote_is_dir(sftp, location.remote_path):
            return None
        for relative_path in _SOP_CACHE_RELATIVE_FILES:
            remote_file = posixpath.join(location.remote_path, *relative_path.split("/"))
            try:
                remote_stat = sftp.stat(remote_file)
                if not stat.S_ISREG(remote_stat.st_mode):
                    continue
                _download_remote_file(
                    sftp,
                    remote_file,
                    location.remote_path,
                    local_project_root,
                )
            except (OSError, PermissionError):
                continue
            downloaded += 1
        if downloaded:
            marker = local_project_root / ".sftp-cache-complete"
            marker.write_text("workflow cache ready\n", encoding="utf-8")
        return local_project_root if downloaded or local_project_root.exists() else None
    finally:
        if client is not None:
            client.close()


def _mirror_sftp_project(
    location: SftpLocation,
    project_id: str,
    *,
    local_project_root: Path | None = None,
) -> Path | None:
    remote_project_root = location.remote_path
    local_project_root = local_project_root or _sftp_cache_root(location, project_id)
    client = None
    try:
        client, sftp = _open_sftp(location)
        if not _remote_is_dir(sftp, remote_project_root):
            return None

        # Remote directory confirmed to exist — create local cache root now so
        # we always return a valid path even when no downloadable files are found
        # (e.g. brand-new project with no result files yet, or unsupported assay
        # type whose file extensions aren't in _REMOTE_CACHE_SUFFIXES).
        local_project_root.mkdir(parents=True, exist_ok=True)

        downloaded = 0
        seen_dirs: set[str] = set()
        pending: list[tuple[str, int]] = []
        remote_dirs = [remote_project_root]
        for report_root in _remote_report_roots(sftp, remote_project_root, project_id):
            remote_dirs.extend(_remote_report_evidence_dirs(remote_project_root, report_root))
        for directory in remote_dirs:
            if _remote_is_dir(sftp, directory):
                pending.append((directory, 0))
        if not pending:
            pending.append((remote_project_root, 0))

        while pending and downloaded < _MAX_INDEXED_FILES:
            current, depth = pending.pop(0)
            if current in seen_dirs:
                continue
            seen_dirs.add(current)
            try:
                children = sorted(
                    sftp.listdir_attr(current),
                    key=lambda item: _sort_remote_child(posixpath.join(current, item.filename)),
                )
            except OSError:
                continue
            for child in children:
                remote_child = posixpath.join(current, child.filename)
                # Follow symlinks: listdir_attr returns lstat() so symlinks to
                # directories appear as S_ISLNK, not S_ISDIR.
                child_is_dir = stat.S_ISDIR(child.st_mode) or (
                    stat.S_ISLNK(child.st_mode) and _remote_is_dir(sftp, remote_child)
                )
                if child_is_dir:
                    if depth < 3 and not _should_skip_remote_dir(remote_child):
                        pending.append((remote_child, depth + 1))
                    continue
                # Also download symlinks to regular files (S_ISLNK that is not a dir).
                if (stat.S_ISREG(child.st_mode) or stat.S_ISLNK(child.st_mode)) and (
                    Path(child.filename).suffix.lower() in _REMOTE_CACHE_SUFFIXES
                    or child.filename.lower() in _INTERNAL_WORKFLOW_NAMES
                ):
                    try:
                        _download_remote_file(sftp, remote_child, remote_project_root, local_project_root)
                    except (OSError, PermissionError):
                        continue
                    downloaded += 1
                    if downloaded >= _MAX_INDEXED_FILES:
                        break
        return local_project_root if downloaded or local_project_root.exists() else None
    finally:
        if client is not None:
            client.close()


def _looks_like_report_root(path: Path) -> bool:
    markers = {
        "1.ReadsQC",
        "2.AlignmentQC",
        "3.Correlation",
        "5.Peakcalling",
        "7.Motif",
        "8.MotifyAnalysis",
    }
    try:
        child_names = {child.name for child in path.iterdir() if child.is_dir()}
    except (OSError, PermissionError):
        return False
    return bool(markers & child_names)


def _report_roots(project_root: Path) -> list[Path]:
    project_id = project_root.name
    candidates = [
        project_root / "final_report",
        project_root / project_id,
        project_root / f"{project_id}_result",
        project_root / "result" / f"{project_id}_result",
    ]
    try:
        for child in project_root.iterdir():
            if not child.is_dir():
                continue
            if child.name in {"final_report", project_id, f"{project_id}_result"} or _looks_like_report_root(child):
                candidates.append(child)
    except (OSError, PermissionError):
        pass

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if not path.exists() or not path.is_dir():
            continue
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def list_report_roots(project_root: Path) -> list[Path]:
    return _report_roots(project_root)


def _report_evidence_dirs(report_root: Path) -> list[Path]:
    return [
        report_root,
        report_root / "1.ReadsQC",
        report_root / "2.AlignmentQC" / "2.1AlignmentStat",
        report_root / "3.Correlation",
        report_root / "4.TSSHeatmap",
        report_root / "5.Peakcalling" / "5.1PeakStat",
        report_root / "5.Peakcalling" / "5.3PeakAnno",
        report_root / "5.Peakcalling" / "5.4Enrichment",
        report_root / "5.Peakcalling" / "5.6peakFrip",
        report_root / "6.forIGV",
        report_root / "7.Motif",
        report_root / "7.DiffAnalysis" / "7.1DiffPeak",
        report_root / "7.DiffAnalysis" / "7.3DiffGOEnrichment",
        report_root / "7.DiffAnalysis" / "7.4DiffPathwayEnrichment",
        report_root / "8.MotifyAnalysis" / "8.1Motify",
    ]


def _common_evidence_dirs(project_root: Path) -> list[Path]:
    project_id = project_root.name
    dirs: list[Path] = []
    for candidate in _report_roots(project_root):
        dirs.extend(_report_evidence_dirs(candidate))
    dirs.extend(
        [
            project_root / "result" / "Spikein",
            project_root / "result" / "deeptools" / "Correlation",
            project_root,
        ]
    )
    return dirs


def _iter_common_evidence_files(project_root: Path) -> list[Path]:
    started_at = monotonic()
    resolved_root = project_root.resolve()
    root_mtime_ns = _get_root_mtime_ns(resolved_root)
    cached = _COMMON_EVIDENCE_FILES_CACHE.get(resolved_root)
    if cached and cached[0] == root_mtime_ns:
        return list(cached[1])

    files: list[Path] = []
    seen_dirs: set[Path] = set()
    for directory in _common_evidence_dirs(resolved_root):
        if monotonic() - started_at >= _MAX_LOCAL_SCAN_SECONDS:
            break
        if not directory.exists() or not directory.is_dir():
            continue
        pending: list[tuple[Path, int]] = [(directory, 0)]
        while (
            pending
            and len(files) < _MAX_COMMON_EVIDENCE_FILES
            and len(seen_dirs) < _MAX_COMMON_EVIDENCE_DIRS
            and monotonic() - started_at < _MAX_LOCAL_SCAN_SECONDS
        ):
            current, depth = pending.pop(0)
            try:
                resolved_current = current.resolve()
            except (OSError, PermissionError):
                continue
            if resolved_current in seen_dirs:
                continue
            seen_dirs.add(resolved_current)
            try:
                children = sorted(current.iterdir(), key=_sort_children_for_index)
            except (OSError, PermissionError):
                continue
            for path in children:
                if path.is_dir():
                    if depth < 3 and not _should_skip_dir(path):
                        pending.append((path, depth + 1))
                    continue
                if path.is_file() and path.suffix.lower() in _INDEXED_SUFFIXES:
                    files.append(path)
                    if len(files) >= _MAX_COMMON_EVIDENCE_FILES:
                        break
    _COMMON_EVIDENCE_FILES_CACHE[resolved_root] = (root_mtime_ns, list(files))
    return files


def _detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".xls", ".tsv", ".tab", ".csv"}:
        return "table"
    if suffix in {".txt", ".log", ".md", ".html", ".htm", ".bed"}:
        return "text"
    return "binary_or_report"


def _get_root_mtime_ns(project_root: Path) -> int:
    return project_root.stat().st_mtime_ns


def _should_skip_dir(path: Path) -> bool:
    name = path.name.lower()
    normalized = str(path).lower().replace("/", "\\")
    return (
        name in _SKIP_DIR_NAMES
        or any(token in normalized for token in _SKIP_DIR_TOKENS)
        or "\\report\\image" in normalized
    )


def _sort_children_for_index(path: Path) -> tuple[int, str]:
    normalized = str(path).lower().replace("/", "\\")
    priority = next(
        (index for index, token in enumerate(_PRIORITY_DIR_TOKENS) if token in normalized),
        len(_PRIORITY_DIR_TOKENS),
    )
    return (priority, path.name.lower())


def _build_project_file_index(project_root: Path) -> ProjectFileIndex:
    started_at = monotonic()
    files: list[ProjectFile] = []
    pending: list[tuple[Path, int]] = [(project_root, 0)]
    seen_dirs: set[Path] = set()
    while (
        pending
        and len(files) < _MAX_INDEXED_FILES
        and len(seen_dirs) < _MAX_INDEXED_DIRS
        and monotonic() - started_at < _MAX_LOCAL_SCAN_SECONDS
    ):
        current, depth = pending.pop(0)
        try:
            resolved_current = current.resolve()
        except (OSError, RuntimeError):
            resolved_current = current
        if resolved_current in seen_dirs:
            continue
        seen_dirs.add(resolved_current)
        try:
            children = sorted(current.iterdir(), key=_sort_children_for_index)
        except (OSError, PermissionError):
            continue
        for path in children:
            if path.is_dir():
                if depth < 3 and not _should_skip_dir(path):
                    pending.append((path, depth + 1))
                continue
            if path.is_file() and path.suffix.lower() in _INDEXED_SUFFIXES:
                files.append(ProjectFile(path=path, kind=_detect_kind(path)))
                if len(files) >= _MAX_INDEXED_FILES:
                    break
    files.sort(key=lambda item: str(item.path))
    return ProjectFileIndex(
        root=project_root,
        root_mtime_ns=_get_root_mtime_ns(project_root),
        files=files,
    )


def _get_project_file_index(project_root: Path) -> ProjectFileIndex:
    resolved_root = project_root.resolve()
    current_mtime_ns = _get_root_mtime_ns(resolved_root)
    cached = _PROJECT_FILE_INDEX_CACHE.get(resolved_root)
    if cached and cached.root_mtime_ns == current_mtime_ns:
        return cached
    rebuilt = _build_project_file_index(resolved_root)
    _PROJECT_FILE_INDEX_CACHE[resolved_root] = rebuilt
    return rebuilt


def resolve_project_root(project_id: str, project_root: str | None = None) -> Path:
    project_id = _validate_project_id(project_id)
    local_candidates: list[Path] = []
    remote_candidates: list[str] = []
    if project_root:
        if _is_sftp_url(project_root):
            remote_candidates.append(project_root)
        else:
            _validate_explicit_local_root(project_root)
            local_candidates.append(Path(project_root))

    local_candidates.extend(_iter_cached_project_roots(project_id))

    for base_dir in _get_project_base_dirs():
        if _is_sftp_url(base_dir):
            remote_candidates.append(base_dir)
            continue
        local_candidates.extend(_iter_local_project_candidates(Path(base_dir), project_id))

    seen_local: set[Path] = set()
    for candidate_path in local_candidates:
        if candidate_path in seen_local:
            continue
        seen_local.add(candidate_path)
        try:
            is_project_dir = candidate_path.exists() and candidate_path.is_dir()
        except (OSError, PermissionError):
            continue
        if is_project_dir:
            return candidate_path.resolve()

    if _sftp_offline():
        raise FileNotFoundError(
            f"Project root not found for project_id={project_id}; SFTP access is disabled by PROJECT_SFTP_OFFLINE"
        )

    seen_remote: set[str] = set()
    for candidate in remote_candidates:
        if candidate in seen_remote:
            continue
        seen_remote.add(candidate)
        try:
            base_location = _parse_sftp_url(candidate)
        except ValueError:
            continue

        remote_project_paths: list[str]
        if project_root and candidate == project_root:
            remote_project_paths = [base_location.remote_path]
        else:
            try:
                remote_project_paths = _iter_remote_project_paths(base_location, project_id)
            except Exception:
                remote_project_paths = []
            # If SFTP search returned nothing (e.g. listdir failed for a Chinese-named
            # intermediate directory), fall back to the direct path and let
            # _mirror_sftp_project do a deeper scan from the project root itself.
            if not remote_project_paths:
                remote_project_paths = [posixpath.join(base_location.remote_path, project_id)]

        for remote_path in remote_project_paths:
            location = SftpLocation(
                host=base_location.host,
                port=base_location.port,
                username=base_location.username,
                password=base_location.password,
                remote_path=remote_path,
                url=_build_sftp_url(base_location, remote_path),
            )
            cached = _sftp_cache_root(location, project_id)
            try:
                if cached.exists() and cached.is_dir():
                    return cached.resolve()
                mirrored = _mirror_sftp_project(location, project_id)
            except Exception:
                continue
            if mirrored and mirrored.exists() and mirrored.is_dir():
                return mirrored.resolve()

    raise FileNotFoundError(f"Project root not found for project_id={project_id}")


def refresh_project_sftp_logs(project_id: str, local_root: Path) -> None:
    """Re-mirror an SFTP project to ensure log files are present in the local cache.

    Called for pipeline_failure questions when find_log_files() returns nothing —
    the original mirror may have been done before the project produced any log files,
    or the log directory was skipped.  _mirror_sftp_project only downloads files that
    are new or whose size changed, so this call is safe to repeat.
    """
    if _sftp_offline():
        return
    project_id = _validate_project_id(project_id)
    for base_dir in _get_project_base_dirs():
        if not _is_sftp_url(base_dir):
            continue
        try:
            base_location = _parse_sftp_url(base_dir)
        except ValueError:
            continue
        try:
            remote_project_paths = _iter_remote_project_paths(base_location, project_id)
        except Exception:
            remote_project_paths = []
        if not remote_project_paths:
            remote_project_paths = [posixpath.join(base_location.remote_path, project_id)]
        for remote_path in remote_project_paths:
            location = SftpLocation(
                host=base_location.host,
                port=base_location.port,
                username=base_location.username,
                password=base_location.password,
                remote_path=remote_path,
                url=_build_sftp_url(base_location, remote_path),
            )
            try:
                _mirror_sftp_project(location, project_id, local_project_root=local_root)
                return  # Done after first successful mirror
            except Exception:
                continue


def list_project_files(project_root: Path, limit: int = 200) -> list[ProjectFile]:
    files = [
        ProjectFile(path=path, kind=_detect_kind(path))
        for path in _iter_common_evidence_files(project_root)[:limit]
    ]
    if files:
        return files
    index = _get_project_file_index(project_root)
    return index.files[:limit]


def read_text_snippet(path: Path, max_lines: int = 500, max_chars: int = 50000) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    snippet = "\n".join(lines[:max_lines])
    return snippet[:max_chars]


def read_log_snippet(path: Path, max_tail_lines: int = 300, max_chars: int = 30000) -> str:
    """Read a log file returning the tail where errors most likely appear.

    For log files the most relevant content (errors, tracebacks, exit codes) is
    almost always at the END of the file, so we return the last *max_tail_lines*
    lines rather than the first ones that read_text_snippet would give.
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    if len(lines) > max_tail_lines:
        snippet = "\n".join(lines[-max_tail_lines:])
    else:
        snippet = text
    return snippet[:max_chars]


def find_log_files(project_root: Path, limit: int = 10) -> list[Path]:
    """Find log files scattered under the project directory."""
    index = _get_project_file_index(project_root)
    log_name_tokens = ("error", "stderr", "stdout", "pipeline", "snakemake", "workflow", "run")
    results: list[Path] = []
    for item in index.files:
        name_lower = item.path.name.lower()
        if item.path.suffix.lower() == ".log":
            results.append(item.path)
        elif any(token in name_lower for token in log_name_tokens) and item.path.suffix.lower() in {
            ".txt",
            ".log",
            "",
        }:
            results.append(item.path)
        if len(results) >= limit * 3:
            break
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in results:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    unique.sort(key=lambda p: (len(p.parts), len(str(p)), str(p)))
    return unique[:limit]


def find_internal_workflow_files(
    project_root: Path,
    limit: int = 6,
    project_config: Mapping[str, object] | None = None,
) -> list[Path]:
    """Find private workflow/config inputs without making them visible evidence."""
    started_at = monotonic()
    root = project_root.resolve()
    scan_roots = [root, *_sop_workflow_roots(root, project_config)]
    preferred_names = _SOP_CACHE_RELATIVE_FILES
    selected: list[Path] = []
    seen: set[Path] = set()

    def add_candidate(path: Path) -> None:
        if len(selected) >= limit or not path.exists() or not path.is_file():
            return
        resolved = path.resolve()
        if resolved in seen:
            return
        if (
            path.name.lower() not in _INTERNAL_WORKFLOW_NAMES
            and path.suffix.lower() not in _INTERNAL_WORKFLOW_SUFFIXES
        ):
            return
        seen.add(resolved)
        selected.append(resolved)

    for scan_root in scan_roots:
        for name in preferred_names:
            add_candidate(scan_root / name)

    pending: list[tuple[Path, int]] = [(scan_root, 0) for scan_root in scan_roots]
    scanned_dirs = 0
    scanned_files = 0
    while (
        pending
        and len(selected) < limit
        and scanned_dirs < _MAX_INTERNAL_WORKFLOW_DIRS
        and scanned_files < _MAX_INTERNAL_WORKFLOW_FILES
        and monotonic() - started_at < _MAX_LOCAL_SCAN_SECONDS
    ):
        current, depth = pending.pop(0)
        scanned_dirs += 1
        try:
            children = sorted(current.iterdir(), key=_sort_children_for_index)
        except (OSError, PermissionError):
            continue
        for path in children:
            if path.is_dir():
                if depth < 2 and not _should_skip_dir(path):
                    pending.append((path, depth + 1))
                continue
            scanned_files += 1
            add_candidate(path)
            if len(selected) >= limit:
                break
    return selected


def read_table_rows(path: Path, limit: int = 5000) -> list[dict[str, str]]:
    encodings = ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin1")
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            with path.open("r", encoding=encoding, errors="strict", newline="") as fh:
                sample = fh.read(4096)
                fh.seek(0)
                delimiter = "\t" if sample.count("\t") >= sample.count(",") else ","
                reader = csv.DictReader(fh, delimiter=delimiter)
                rows: list[dict[str, str]] = []
                for row in reader:
                    if row is None:
                        continue
                    rows.append({str(key or "").strip(): (value or "").strip() for key, value in row.items()})
                    if len(rows) >= limit:
                        break
                if rows:
                    return rows
        except Exception as exc:
            last_error = exc
            continue
    raise ValueError(f"Unable to parse table file: {path}") from last_error


def find_files(project_root: Path, keywords: Iterable[str], limit: int = 10) -> list[Path]:
    lowered_keywords = [keyword.lower() for keyword in keywords if keyword]
    common_files = _iter_common_evidence_files(project_root)
    common_matches: list[Path] = []
    for path in common_files:
        haystack = str(path).lower()
        if any(keyword in haystack for keyword in lowered_keywords):
            common_matches.append(path)
    if common_matches:
        common_matches.sort(
            key=lambda item: (
                len(item.parts),
                len(str(item)),
                str(item),
            )
        )
        return common_matches[:limit]
    if common_files:
        return []

    matched: list[Path] = []
    index = _get_project_file_index(project_root)
    for item in index.files:
        haystack = str(item.path).lower()
        if any(keyword in haystack for keyword in lowered_keywords):
            matched.append(item.path)
    matched.sort(
        key=lambda item: (
            "report\\image" in str(item).lower().replace("/", "\\"),
            len(item.parts),
            len(str(item)),
            str(item),
        )
    )
    return matched[:limit]
