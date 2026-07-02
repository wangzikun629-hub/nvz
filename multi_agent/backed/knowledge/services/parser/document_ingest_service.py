"""PDF / PPT → 页面图片转换服务。

PPT 转图片优先级：
  1. win32com（直接让 PowerPoint 逐页导出 PNG，零中间文件）
  2. LibreOffice → PDF，再由 pymupdf 转图片
  3. 均不可用时抛出带友好提示的 RuntimeError
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile

from multi_agent.backed.knowledge.config.settings import settings

logger = logging.getLogger(__name__)

# Windows 下 LibreOffice 常见安装路径
_LIBREOFFICE_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def _find_libreoffice() -> str | None:
    """返回可用的 LibreOffice 可执行文件路径，找不到返回 None。"""
    if shutil.which("libreoffice"):
        return "libreoffice"
    if shutil.which("soffice"):
        return "soffice"
    for candidate in _LIBREOFFICE_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None


class DocumentIngestService:
    def convert_to_images_bg(
        self,
        document_id: str,
        file_path: str,
        file_ext: str,
        doc_repo,
    ) -> None:
        """后台任务：将 PDF/PPT 转为页面图片并更新 DB 状态。"""
        try:
            doc_repo.update_status(document_id, "converting")

            if file_ext in (".ppt", ".pptx"):
                # 优先直接导出（跳过 PDF），失败则 fallback 到 PDF 中转
                image_paths = self._ppt_to_images(file_path, document_id)
            elif file_ext in (".doc", ".docx", ".xls", ".xlsx"):
                # Word / Excel：经 LibreOffice 转 PDF 再转图片
                image_paths = self._office_to_images(file_path, document_id)
            else:
                image_paths = self._pdf_to_images(file_path, document_id)

            page_count = len(image_paths)
            doc_repo.set_page_images(document_id, page_count, image_paths)
            logger.info("convert done document_id=%s pages=%d", document_id, page_count)
        except Exception as exc:
            logger.exception("convert failed document_id=%s", document_id)
            doc_repo.update_status(document_id, "convert_failed", parse_error=str(exc))

    # ──────────────────────────────────────────────────────────────────────
    # PPT → 图片（直接，无 PDF 中间文件）
    # ──────────────────────────────────────────────────────────────────────

    def _ppt_to_images(self, ppt_path: str, document_id: str) -> list[str]:
        """将 PPT/PPTX 转为图片列表。

        优先 win32com 直接导出；不可用时经 LibreOffice→PDF→图片。
        """
        # ── 方案 1：win32com 直接导出 PNG ─────────────────────────────────
        if sys.platform == "win32":
            try:
                return self._ppt_to_images_via_com(ppt_path, document_id)
            except ImportError:
                logger.debug("pywin32 未安装，跳过 COM 方案")
            except Exception as exc:
                logger.warning("COM 直接导出失败，尝试 LibreOffice 路径：%s", exc)

        # ── 方案 2：LibreOffice → PDF → pymupdf ───────────────────────────
        lo_exe = _find_libreoffice()
        if lo_exe:
            pdf_path = self._convert_to_pdf_via_libreoffice(ppt_path, lo_exe)
            try:
                return self._pdf_to_images(pdf_path, document_id)
            finally:
                # 清理临时 PDF
                try:
                    os.remove(pdf_path)
                except OSError:
                    pass

        raise RuntimeError(
            "PPT 转换不可用：请安装 Microsoft Office（推荐）或 LibreOffice，"
            "或执行 `pip install pywin32` 以启用 COM 转换。"
        )

    # ──────────────────────────────────────────────────────────────────────
    # Word / Excel → 图片（经 LibreOffice 转 PDF 中转）
    # ──────────────────────────────────────────────────────────────────────

    def _office_to_images(self, file_path: str, document_id: str) -> list[str]:
        """将 Word/Excel 文档转为图片列表（经 LibreOffice → PDF → pymupdf）。"""
        lo_exe = _find_libreoffice()
        if not lo_exe:
            raise RuntimeError(
                "Word/Excel 转换不可用：请安装 LibreOffice 后重试。"
            )
        pdf_path = self._convert_to_pdf_via_libreoffice(file_path, lo_exe)
        try:
            return self._pdf_to_images(pdf_path, document_id)
        finally:
            try:
                os.remove(pdf_path)
            except OSError:
                pass

    def _ppt_to_images_via_com(self, ppt_path: str, document_id: str) -> list[str]:
        """用 PowerPoint COM 接口将每张幻灯片直接导出为 PNG（Windows 专用）。"""
        import pythoncom  # noqa: PLC0415
        import win32com.client  # noqa: PLC0415

        ppt_path_abs = os.path.abspath(ppt_path)
        output_dir = os.path.join(settings.PARSER_PAGE_IMAGES_DIR, document_id)
        os.makedirs(output_dir, exist_ok=True)

        dpi = getattr(settings, "PARSER_IMAGE_DPI", 150)
        # PowerPoint 默认幻灯片尺寸 10×7.5 英寸；按 DPI 换算像素
        width_px = int(10 * dpi)
        height_px = int(7.5 * dpi)

        pythoncom.CoInitialize()
        try:
            ppt_app = win32com.client.Dispatch("PowerPoint.Application")
            presentation = ppt_app.Presentations.Open(  # type: ignore[attr-defined]
                ppt_path_abs, ReadOnly=True, Untitled=False, WithWindow=False
            )
            image_paths: list[str] = []
            try:
                slide_count = presentation.Slides.Count  # type: ignore[attr-defined]
                for i in range(1, slide_count + 1):
                    out_path = os.path.join(output_dir, f"page_{i:04d}.png")
                    presentation.Slides(i).Export(out_path, "PNG", width_px, height_px)  # type: ignore[attr-defined]
                    image_paths.append(out_path)
            finally:
                presentation.Close()
                ppt_app.Quit()
        finally:
            pythoncom.CoUninitialize()

        if not image_paths:
            raise RuntimeError("PowerPoint COM 未导出任何图片")
        return image_paths

    # ──────────────────────────────────────────────────────────────────────
    # LibreOffice PPT → PDF（备用路径）
    # ──────────────────────────────────────────────────────────────────────

    def _convert_to_pdf_via_libreoffice(self, file_path: str, lo_exe: str) -> str:
        """通过 LibreOffice 将 PPT/Word/Excel 等文档转为 PDF，返回临时 PDF 路径。"""
        out_dir = tempfile.mkdtemp()
        cmd = [lo_exe, "--headless", "--convert-to", "pdf", "--outdir", out_dir, file_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"LibreOffice 转换失败：{result.stderr}")
        base = os.path.splitext(os.path.basename(file_path))[0]
        pdf_path = os.path.join(out_dir, f"{base}.pdf")
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"LibreOffice 输出文件未找到：{pdf_path}")
        return pdf_path

    def _pdf_to_images(self, pdf_path: str, document_id: str) -> list[str]:
        """使用 pymupdf 将 PDF 各页转为 PNG 图片，返回图片路径列表。"""
        try:
            import fitz  # pymupdf
        except ImportError as exc:
            raise RuntimeError("pymupdf 未安装，请执行 pip install pymupdf") from exc

        output_dir = os.path.join(settings.PARSER_PAGE_IMAGES_DIR, document_id)
        os.makedirs(output_dir, exist_ok=True)

        dpi = settings.PARSER_IMAGE_DPI
        mat = fitz.Matrix(dpi / 72, dpi / 72)

        doc = fitz.open(pdf_path)
        image_paths: list[str] = []
        try:
            for page_index in range(len(doc)):
                page = doc[page_index]
                pix = page.get_pixmap(matrix=mat)
                image_path = os.path.join(output_dir, f"page_{page_index + 1:04d}.png")
                pix.save(image_path)
                image_paths.append(image_path)
        finally:
            doc.close()

        return image_paths
