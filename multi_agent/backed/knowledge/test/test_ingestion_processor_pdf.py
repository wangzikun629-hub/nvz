import sys
import types
import unittest
import importlib
from types import SimpleNamespace


markdownify_module = types.ModuleType("markdownify")
markdownify_module.markdownify = lambda text, heading_style=None: text
sys.modules.setdefault("markdownify", markdownify_module)

vector_store_module = types.ModuleType("multi_agent.backed.knowledge.repositories.vector_store_repository")


class DummyVectorStoreRepository:
    def add_documents(self, documents):
        return len(documents)


vector_store_module.VectorStoreRepository = DummyVectorStoreRepository
sys.modules.setdefault(
    "multi_agent.backed.knowledge.repositories.vector_store_repository",
    vector_store_module,
)

langchain_openai_module = types.ModuleType("langchain_openai")


class DummyChatOpenAI:
    def __init__(self, *args, **kwargs):
        pass

    def invoke(self, prompt):
        return SimpleNamespace(content=prompt)


langchain_openai_module.ChatOpenAI = DummyChatOpenAI
sys.modules.setdefault("langchain_openai", langchain_openai_module)

ingestion_module_name = "multi_agent.backed.knowledge.services.ingestion.ingestion_processor"
sys.modules.pop(ingestion_module_name, None)
importlib.import_module("multi_agent.backed.knowledge.services")
importlib.import_module("multi_agent.backed.knowledge.services.ingestion")
ingestion_module = importlib.import_module(ingestion_module_name)
IngestionProcessor = ingestion_module.IngestionProcessor


def _build_processor() -> IngestionProcessor:
    processor = IngestionProcessor.__new__(IngestionProcessor)
    processor.mineru_enabled = False
    return processor


def _install_fake_pypdf_loader(monkeypatch, documents=None, error=None):
    langchain_community_module = types.ModuleType("langchain_community")
    document_loaders_module = types.ModuleType("langchain_community.document_loaders")

    class FakePyPDFLoader:
        def __init__(
            self,
            file_path: str,
            password=None,
            headers=None,
            extract_images=False,
            *,
            mode="page",
            images_parser=None,
            images_inner_format="text",
            pages_delimiter="\n\f",
            extraction_mode="plain",
            extraction_kwargs=None,
        ):
            self.file_path = file_path
            self.mode = mode
            self.extraction_mode = extraction_mode

        def load(self):
            if error is not None:
                raise error
            return documents or []

    document_loaders_module.PyPDFLoader = FakePyPDFLoader
    langchain_community_module.document_loaders = document_loaders_module

    monkeypatch.setitem(sys.modules, "langchain_community", langchain_community_module)
    monkeypatch.setitem(sys.modules, "langchain_community.document_loaders", document_loaders_module)


class SysModulesMonkeyPatch:
    def setitem(self, mapping, key, value):
        mapping[key] = value


class IngestionProcessorPdfTests(unittest.TestCase):
    def setUp(self):
        self.monkeypatch = SysModulesMonkeyPatch()
        self.original_enable_ai = ingestion_module.settings.ENABLE_AI_PREPROCESS_FOR_COMPLEX_DOCS
        self.original_max_chars = ingestion_module.settings.AI_PREPROCESS_MAX_CHARS

    def tearDown(self):
        ingestion_module.settings.ENABLE_AI_PREPROCESS_FOR_COMPLEX_DOCS = self.original_enable_ai
        ingestion_module.settings.AI_PREPROCESS_MAX_CHARS = self.original_max_chars

    def test_convert_pdf_to_markdown_uses_langchain_pypdf_loader_with_layout(self):
        processor = _build_processor()
        _install_fake_pypdf_loader(
            self.monkeypatch,
            documents=[
                SimpleNamespace(page_content="Title\nLine 1\n\n\nLine 2"),
                SimpleNamespace(page_content=""),
                SimpleNamespace(page_content="Cell A   Cell B\nValue A  Value B"),
            ],
        )

        markdown = processor._convert_pdf_to_markdown("sample.pdf")

        self.assertEqual(
            markdown,
            "## Page 1\n\nTitle\nLine 1\n\nLine 2\n\n## Page 3\n\nCell A   Cell B\nValue A  Value B",
        )

    def test_normalize_pdf_page_content_preserves_layout_lines(self):
        processor = _build_processor()

        normalized = processor._normalize_pdf_page_content("Header  \nBody\x00\n\n\nFooter  ")

        self.assertEqual(normalized, "Header\nBody\n\nFooter")

    def test_convert_pdf_to_markdown_raises_when_loader_fails(self):
        processor = _build_processor()
        _install_fake_pypdf_loader(self.monkeypatch, error=RuntimeError("loader failed"))

        with self.assertRaises(RuntimeError) as exc_info:
            processor._convert_pdf_to_markdown("broken.pdf")

        self.assertIn("PDF parsing failed with LangChain PyPDFLoader: loader failed", str(exc_info.exception))

    def test_convert_pdf_to_markdown_raises_clear_error_when_no_text_layer(self):
        processor = _build_processor()
        _install_fake_pypdf_loader(self.monkeypatch, documents=[])

        with self.assertRaises(RuntimeError) as exc_info:
            processor._convert_pdf_to_markdown("scanned.pdf")

        self.assertIn("Please convert it to DOCX or a text-based PDF", str(exc_info.exception))

    def test_maybe_normalize_markdown_with_ai_only_for_complex_docs(self):
        processor = _build_processor()
        processor.llm = SimpleNamespace(invoke=lambda prompt: SimpleNamespace(content="# Cleaned"))
        ingestion_module.settings.ENABLE_AI_PREPROCESS_FOR_COMPLEX_DOCS = True
        ingestion_module.settings.AI_PREPROCESS_MAX_CHARS = 1000

        result = processor._maybe_normalize_markdown_with_ai("sample.pdf", "raw content")
        skipped = processor._maybe_normalize_markdown_with_ai("sample.md", "raw content")

        self.assertEqual(result, "# Cleaned")
        self.assertEqual(skipped, "raw content")

    def test_maybe_normalize_markdown_with_ai_skips_over_limit(self):
        processor = _build_processor()
        processor.llm = SimpleNamespace(
            invoke=lambda prompt: self.fail("AI normalization should be skipped for oversized content")
        )
        ingestion_module.settings.ENABLE_AI_PREPROCESS_FOR_COMPLEX_DOCS = True
        ingestion_module.settings.AI_PREPROCESS_MAX_CHARS = 5

        result = processor._maybe_normalize_markdown_with_ai("sample.docx", "123456")

        self.assertEqual(result, "123456")

    def test_normalize_markdown_with_ai_falls_back_on_empty_response(self):
        processor = _build_processor()
        processor.llm = SimpleNamespace(invoke=lambda prompt: SimpleNamespace(content=""))

        result = processor._normalize_markdown_with_ai("sample.pdf", "raw content")

        self.assertEqual(result, "raw content")

    def test_convert_pptx_to_markdown_uses_mineru_when_enabled(self):
        processor = _build_processor()
        processor.mineru_enabled = True
        processor._convert_document_to_markdown_via_mineru = lambda file_path: f"mineru:{file_path}"
        processor._convert_unstructured_to_markdown = lambda file_path: self.fail("unstructured should not be used")

        markdown = processor._convert_file_to_markdown("deck.pptx")

        self.assertEqual(markdown, "mineru:deck.pptx")

    def test_convert_pptx_to_markdown_falls_back_to_unstructured_when_mineru_fails(self):
        processor = _build_processor()
        processor.mineru_enabled = True
        processor._convert_document_to_markdown_via_mineru = lambda file_path: (_ for _ in ()).throw(
            RuntimeError("mineru failed")
        )
        processor._convert_unstructured_to_markdown = lambda file_path: f"unstructured:{file_path}"

        markdown = processor._convert_file_to_markdown("deck.pptx")

        self.assertEqual(markdown, "unstructured:deck.pptx")


if __name__ == "__main__":
    unittest.main()
