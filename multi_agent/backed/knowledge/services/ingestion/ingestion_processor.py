import csv
import json
import logging
import os.path
import re
import tempfile
import time
import zipfile
from pathlib import Path

import requests
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores.utils import filter_complex_metadata
from markdownify import markdownify as html_to_markdown

from multi_agent.backed.knowledge.config.settings import settings
from multi_agent.backed.knowledge.repositories.vector_store_repository import VectorStoreRepository
from multi_agent.backed.knowledge.utils.markdown_utils import MarkDownUtils


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("unstructured").setLevel(logging.WARNING)


class IngestionProcessor:
    AI_PREPROCESS_EXTENSIONS = {
        ".pdf",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".epub",
        ".odt",
    }

    def __init__(self):
        self.vector_store = VectorStoreRepository()
        self.document_spliter = RecursiveCharacterTextSplitter(
            chunk_size=1500,
            chunk_overlap=200,
            separators=["\n##", "##", "\n###", "\\###", "\n**", "\n\n", "\n", " ", ""],
        )
        self.llm = None
        if settings.ENABLE_AI_PREPROCESS_FOR_COMPLEX_DOCS:
            self.llm = ChatOpenAI(
                model=settings.MODEL,
                api_key=settings.API_KEY,
                base_url=settings.BASE_URL,
                temperature=0,
                timeout=120,
            )
        self.mineru_enabled = (
            settings.ENABLE_MINERU_PDF
            and bool(settings.MINERU_BASE_URL)
            and bool(settings.MINERU_API_TOKEN)
        )

    def _load_documents_as_markdown(self, file_path: str, source_name: str | None = None) -> list[Document]:
        markdown_text = self._convert_file_to_markdown(file_path)
        if not markdown_text.strip():
            raise ValueError(f"file content is empty after markdown conversion: {file_path}")

        markdown_text = self._maybe_normalize_markdown_with_ai(file_path, markdown_text)
        display_name = source_name or MarkDownUtils.extract_title(file_path)

        return [
            Document(
                page_content=markdown_text,
                metadata={
                    "source": display_name,
                    "title": display_name,
                    "source_name": display_name,
                    "path": file_path,
                    "original_extension": os.path.splitext(file_path)[1].lower(),
                    "content_format": "markdown",
                },
            )
        ]

    def _build_document_chunks(
        self,
        file_path: str,
        kb_scope: str | None = None,
        source_name: str | None = None,
        extra_metadata: dict | None = None,
    ) -> list[Document]:
        try:
            documents = self._load_documents_as_markdown(file_path, source_name=source_name)
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            logger.error("文件：%s没有加载到,原因:%s", file_path, str(exc))
            raise RuntimeError(f"文件：{file_path}没有加载到,原因:{str(exc)}") from exc

        scope = (kb_scope or settings.DEFAULT_KB_SCOPE).strip() or settings.DEFAULT_KB_SCOPE
        display_name = source_name or MarkDownUtils.extract_title(file_path)
        metadata_patch = dict(extra_metadata or {})
        for doc in documents:
            doc.metadata["title"] = display_name
            doc.metadata["source_name"] = display_name
            doc.metadata["source"] = display_name
            doc.metadata[settings.MILVUS_PARTITION_KEY] = scope
            doc.metadata.update(metadata_patch)

        final_document_chunks = []
        for doc in documents:
            if len(doc.page_content) < 1500:
                final_document_chunks.append(doc)
                continue

            document_chunks = self.document_spliter.split_documents([doc])
            final_document_chunks.extend(document_chunks)

        return filter_complex_metadata(final_document_chunks)

    def preview_file_chunks(self, file_path: str) -> list[dict]:
        documents = self._build_document_chunks(file_path)
        previews = []
        for index, document in enumerate(documents, start=1):
            content = document.page_content or ""
            previews.append(
                {
                    "chunk_index": index,
                    "length": len(content),
                    "preview": content[:200],
                    "content": content,
                    "metadata": dict(document.metadata),
                }
            )
        return previews

    def add_document_chunks(self, documents: list[Document]) -> int:
        return self.vector_store.add_documents(documents)

    def _convert_file_to_markdown(self, file_path: str) -> str:
        extension = os.path.splitext(file_path)[1].lower()

        if extension in {".md", ".markdown", ".txt", ".log"}:
            return self._read_text_file(file_path)

        if extension in {".csv", ".tsv"}:
            delimiter = "\t" if extension == ".tsv" else ","
            return self._convert_csv_to_markdown(file_path, delimiter)

        if extension in {".html", ".htm"}:
            return html_to_markdown(self._read_text_file(file_path), heading_style="ATX")

        if extension == ".json":
            return f"```json\n{self._format_json_file(file_path)}\n```"

        if extension in {".yaml", ".yml"}:
            return f"```yaml\n{self._read_text_file(file_path)}\n```"

        if extension == ".xml":
            return f"```xml\n{self._read_text_file(file_path)}\n```"

        if extension in {".py", ".js", ".ts", ".java", ".go", ".sql", ".sh", ".ps1", ".css"}:
            language = extension.lstrip(".")
            return f"```{language}\n{self._read_text_file(file_path)}\n```"

        if extension == ".pdf":
            return self._convert_pdf_to_markdown(file_path)

        if extension in {".ppt", ".pptx"}:
            return self._convert_presentation_to_markdown(file_path)

        if extension in {".doc", ".docx", ".xls", ".xlsx", ".epub", ".odt"}:
            return self._convert_unstructured_to_markdown(file_path)

        raise ValueError(
            f"unsupported file type: {extension}. Supported types: "
            ".md, .txt, .csv, .tsv, .html, .json, .yaml, .xml, code files, "
            ".pdf, .doc/.docx, .ppt/.pptx, .xls/.xlsx"
        )

    def _read_text_file(self, file_path: str) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gbk"):
            try:
                with open(file_path, "r", encoding=encoding) as file:
                    return file.read()
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError("utf-8", b"", 0, 1, f"cannot decode text file: {file_path}")

    def _format_json_file(self, file_path: str) -> str:
        text = self._read_text_file(file_path)
        try:
            return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return text

    def _convert_csv_to_markdown(self, file_path: str, delimiter: str) -> str:
        with open(file_path, "r", encoding="utf-8-sig", newline="") as file:
            rows = list(csv.reader(file, delimiter=delimiter))

        if not rows:
            return ""

        max_columns = max(len(row) for row in rows)
        normalized_rows = [row + [""] * (max_columns - len(row)) for row in rows]
        header = normalized_rows[0]
        body = normalized_rows[1:]

        def clean_cell(value: str) -> str:
            return str(value).replace("|", "\\|").replace("\n", "<br>").strip()

        lines = [
            "| " + " | ".join(clean_cell(cell) for cell in header) + " |",
            "| " + " | ".join("---" for _ in header) + " |",
        ]
        lines.extend("| " + " | ".join(clean_cell(cell) for cell in row) + " |" for row in body)
        return "\n".join(lines)

    def _convert_pdf_to_markdown(self, file_path: str) -> str:
        if self.mineru_enabled:
            try:
                logger.info("PDF parsing route=mineru file=%s", file_path)
                return self._convert_document_to_markdown_via_mineru(file_path)
            except Exception as exc:
                logger.warning("MinerU PDF parsing failed for %s, fallback to local parser: %s", file_path, str(exc))

        try:
            logger.info("PDF parsing route=local_pypdf file=%s", file_path)
            from langchain_community.document_loaders import PyPDFLoader

            loader = PyPDFLoader(
                file_path,
                mode="page",
                extraction_mode="layout",
            )
            documents = loader.load()
            pages = []
            for page_index, document in enumerate(documents, start=1):
                text = self._normalize_pdf_page_content(document.page_content or "")
                if text:
                    pages.append(f"## Page {page_index}\n\n{text}")
            if pages:
                return "\n\n".join(pages)
        except Exception as exc:
            raise RuntimeError(f"PDF parsing failed with LangChain PyPDFLoader: {exc}") from exc

        raise RuntimeError(
            "PDF text extraction returned empty content. This PDF likely has no usable text layer "
            "(for example, it may be scanned or image-based). Please convert it to DOCX or a text-based PDF and upload again."
        )

    def _convert_presentation_to_markdown(self, file_path: str) -> str:
        if self.mineru_enabled:
            try:
                logger.info("presentation parsing route=mineru file=%s", file_path)
                return self._convert_document_to_markdown_via_mineru(file_path)
            except Exception as exc:
                logger.warning("MinerU presentation parsing failed for %s, fallback to unstructured: %s", file_path, str(exc))

        logger.info("presentation parsing route=unstructured file=%s", file_path)
        return self._convert_unstructured_to_markdown(file_path)

    def _convert_pdf_to_markdown_via_mineru(self, file_path: str) -> str:
        return self._convert_document_to_markdown_via_mineru(file_path)

    def _convert_document_to_markdown_via_mineru(self, file_path: str) -> str:
        headers = {
            "Authorization": f"Bearer {settings.MINERU_API_TOKEN}",
            "Content-Type": "application/json",
        }
        request_payload = {
            "files": [{"name": Path(file_path).name}],
            "model_version": settings.MINERU_MODEL_VERSION,
        }

        upload_response = requests.post(
            f"{settings.MINERU_BASE_URL}/file-urls/batch",
            json=request_payload,
            headers=headers,
            timeout=30,
        )
        upload_response.raise_for_status()
        upload_data = upload_response.json()
        if upload_data.get("code") != 0:
            raise RuntimeError(f"MinerU upload init failed: {upload_data.get('msg') or upload_data}")

        batch_data = upload_data.get("data") or {}
        file_urls = batch_data.get("file_urls") or []
        batch_id = batch_data.get("batch_id")
        if not file_urls or not batch_id:
            raise RuntimeError("MinerU upload init returned empty file_urls or batch_id")
        logger.info("MinerU upload initialized file=%s batch_id=%s model_version=%s", file_path, batch_id, settings.MINERU_MODEL_VERSION)

        with open(file_path, "rb") as document_file:
            with requests.Session() as session:
                session.trust_env = False
                put_response = session.put(
                    file_urls[0],
                    data=document_file.read(),
                    timeout=60,
                )
        put_response.raise_for_status()

        full_zip_url = self._poll_mineru_result(batch_id, headers)
        return self._download_markdown_from_mineru_zip(full_zip_url, Path(file_path).stem)

    def _poll_mineru_result(self, batch_id: str, headers: dict[str, str]) -> str:
        poll_url = f"{settings.MINERU_BASE_URL}/extract-results/batch/{batch_id}"
        started_at = time.time()

        while time.time() - started_at <= settings.MINERU_TIMEOUT_SECONDS:
            response = requests.get(poll_url, headers=headers, timeout=15)
            if 500 <= response.status_code < 600:
                time.sleep(settings.MINERU_POLL_INTERVAL_SECONDS)
                continue
            response.raise_for_status()

            poll_data = response.json()
            if poll_data.get("code") != 0:
                raise RuntimeError(f"MinerU polling failed: {poll_data.get('msg') or poll_data}")

            extract_results = ((poll_data.get("data") or {}).get("extract_result")) or []
            if not extract_results:
                time.sleep(settings.MINERU_POLL_INTERVAL_SECONDS)
                continue

            task_state = extract_results[0].get("state")
            if task_state == "done":
                full_zip_url = extract_results[0].get("full_zip_url")
                if not full_zip_url:
                    raise RuntimeError("MinerU task completed without full_zip_url")
                logger.info("MinerU parsing completed batch_id=%s elapsed=%.2fs", batch_id, time.time() - started_at)
                return full_zip_url
            if task_state == "failed":
                raise RuntimeError("MinerU PDF parsing task failed")

            time.sleep(settings.MINERU_POLL_INTERVAL_SECONDS)

        raise TimeoutError(f"MinerU PDF parsing timed out after {settings.MINERU_TIMEOUT_SECONDS} seconds")

    def _download_markdown_from_mineru_zip(self, zip_url: str, file_stem: str) -> str:
        response = requests.get(zip_url, timeout=120)
        response.raise_for_status()

        with tempfile.TemporaryDirectory(prefix="mineru_pdf_") as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / f"{file_stem}.zip"
            extract_dir = temp_path / file_stem
            zip_path.write_bytes(response.content)
            extract_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_dir)

            md_files = list(extract_dir.rglob("*.md"))
            if not md_files:
                raise FileNotFoundError("MinerU result zip does not contain markdown files")

            target_md_file = next((md for md in md_files if md.stem == file_stem), None)
            if target_md_file is None:
                target_md_file = next((md for md in md_files if md.name.lower() == "full.md"), md_files[0])

            logger.info("MinerU markdown selected file_stem=%s markdown_file=%s markdown_candidates=%d", file_stem, target_md_file, len(md_files))
            return target_md_file.read_text(encoding="utf-8").strip()

    def _normalize_pdf_page_content(self, text: str) -> str:
        text = text.replace("\x00", "").replace("\uf0b7", "-")
        lines = [line.rstrip() for line in text.splitlines()]
        normalized = "\n".join(lines).strip()
        return re.sub(r"\n{3,}", "\n\n", normalized)

    def _maybe_normalize_markdown_with_ai(self, file_path: str, markdown_text: str) -> str:
        extension = os.path.splitext(file_path)[1].lower()
        if extension not in self.AI_PREPROCESS_EXTENSIONS:
            return markdown_text
        if not settings.ENABLE_AI_PREPROCESS_FOR_COMPLEX_DOCS or not self.llm:
            return markdown_text
        if len(markdown_text) > settings.AI_PREPROCESS_MAX_CHARS:
            logger.info(
                "Skip AI preprocessing for %s because content length %s exceeds limit %s",
                file_path,
                len(markdown_text),
                settings.AI_PREPROCESS_MAX_CHARS,
            )
            return markdown_text

        return self._normalize_markdown_with_ai(file_path, markdown_text)

    def _normalize_markdown_with_ai(self, file_path: str, markdown_text: str) -> str:
        prompt = f"""
你是一个文档整理器。你的任务是整理提取后的文档内容，输出更稳定的 Markdown，供后续切分入库。

要求：
1. 只允许整理原文，不允许总结、解释、补充、改写事实，不允许添加原文中不存在的信息。
2. 尽量保留原始结构，包括标题层级、列表、表格、页码标记、章节顺序、术语、数字、单位、符号。
3. 修复明显的断行、碎片段落、重复空行、乱码符号；但不要删除真实内容。
4. 如果看到表格，尽量整理为 Markdown 表格；如果无法可靠还原，保留原始行结构，不要编造单元格。
5. 不要输出任何说明文字，只输出整理后的 Markdown 正文。

文件名：{os.path.basename(file_path)}

原始内容：
```markdown
{markdown_text}
```
""".strip()

        try:
            response = self.llm.invoke(prompt)
        except Exception as exc:
            logger.warning("AI preprocessing failed for %s, fallback to original markdown: %s", file_path, str(exc))
            return markdown_text

        normalized_text = (response.content or "").strip()
        if not normalized_text:
            logger.warning("AI preprocessing returned empty content for %s, fallback to original markdown", file_path)
            return markdown_text
        return normalized_text

    def _convert_unstructured_to_markdown(self, file_path: str) -> str:
        try:
            from unstructured.partition.auto import partition
        except ImportError as exc:
            raise RuntimeError("unstructured is required to parse this file type") from exc

        elements = partition(filename=file_path)
        return self._elements_to_markdown(elements)

    def _elements_to_markdown(self, elements) -> str:
        blocks = []
        for element in elements:
            text = str(element).strip()
            if not text:
                continue

            category = getattr(element, "category", "")
            metadata = getattr(element, "metadata", None)
            text_as_html = getattr(metadata, "text_as_html", None) if metadata else None

            if category == "Title":
                blocks.append(f"## {text}")
            elif category == "ListItem":
                blocks.append(f"- {text}")
            elif category == "Table" and text_as_html:
                blocks.append(html_to_markdown(text_as_html, heading_style="ATX").strip())
            else:
                blocks.append(text)

        return "\n\n".join(blocks)

    def ingest_file(self, md_path: str, kb_scope: str | None = None) -> int:
        clean_documents_chunks = self._build_document_chunks(md_path, kb_scope=kb_scope)
        return self.add_document_chunks(clean_documents_chunks)
