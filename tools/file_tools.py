"""
文件解析模块 — 参考 Anthropic skills 的最佳实践。

DOCX: 使用 pandoc（Anthropic docx skill 推荐）
PDF:  使用 pdfplumber（Anthropic pdf skill 推荐）
"""
import io
import os
import subprocess
import tempfile
import pdfplumber
from core.logging import get_logger
from core.exceptions import ResumeParseError
from tools.base import tool_call, with_retry

logger = get_logger("tools.file")


@tool_call("extract_text_from_pdf")
@with_retry()
def extract_text_from_pdf(content: bytes) -> str:
    """
    从 PDF 字节中提取纯文本。
    使用 pdfplumber（Anthropic pdf skill 推荐）。
    """
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        text = "\n".join(pages).strip()

        # 如果 pdfplumber 没提取到文本（扫描件），尝试 OCR
        if not text:
            logger.warning("pdfplumber_empty_trying_ocr")
            text = _ocr_pdf(content)

        return text
    except Exception as exc:
        raise ResumeParseError(f"PDF parse failed: {exc}") from exc


def _ocr_pdf(content: bytes) -> str:
    """对扫描件 PDF 进行 OCR（需要 pytesseract + pdf2image）。"""
    try:
        from pdf2image import convert_from_bytes
        import pytesseract

        images = convert_from_bytes(content, dpi=200)
        texts = []
        for i, img in enumerate(images):
            page_text = pytesseract.image_to_string(img, lang="chi_sim+eng")
            if page_text.strip():
                texts.append(page_text)
        return "\n".join(texts).strip()
    except ImportError:
        logger.warning("ocr_not_available_install_pytesseract_pdf2image")
        return ""
    except Exception as exc:
        logger.warning("ocr_failed", error=str(exc))
        return ""


@tool_call("extract_text_from_docx")
@with_retry()
def extract_text_from_docx(content: bytes) -> str:
    """
    从 DOCX 字节中提取纯文本。
    使用 pandoc（Anthropic docx skill 推荐的方法）。
    """
    try:
        # 方法 1: pandoc（最健壮，Anthropic 推荐）
        text = _extract_docx_via_pandoc(content)
        if text and len(text) > 50:
            return text

        # 方法 2: XML 解析（备用，提取更多文本）
        logger.warning("pandoc_insufficient_trying_xml", pandoc_len=len(text or ""))
        xml_text = _extract_docx_via_xml(content)
        if xml_text and len(xml_text) > len(text or ""):
            return xml_text

        # 方法 3: python-docx（最后备用）
        if not text:
            logger.warning("xml_failed_trying_python_docx")
            return _extract_docx_via_python_docx(content)

        return text

    except Exception as exc:
        raise ResumeParseError(f"DOCX parse failed: {exc}") from exc


def _extract_docx_via_pandoc(content: bytes) -> str:
    """使用 pandoc 提取 DOCX 文本（Anthropic docx skill 推荐）。"""
    try:
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        result = subprocess.run(
            ["pandoc", "-t", "markdown", tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        os.unlink(tmp_path)

        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return ""

    except FileNotFoundError:
        logger.warning("pandoc_not_found")
        return ""
    except Exception as exc:
        logger.warning("pandoc_failed", error=str(exc))
        return ""


def _extract_docx_via_xml(content: bytes) -> str:
    """通过 XML 解析提取 DOCX 文本。"""
    try:
        import zipfile
        import xml.etree.ElementTree as ET

        z = zipfile.ZipFile(io.BytesIO(content))
        with z.open("word/document.xml") as f:
            xml_content = f.read().decode("utf-8")

        root = ET.fromstring(xml_content)
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        texts = []
        for elem in root.iter(f"{{{ns}}}t"):
            if elem.text:
                texts.append(elem.text)

        return " ".join(texts).strip()
    except Exception:
        return ""


def _extract_docx_via_python_docx(content: bytes) -> str:
    """使用 python-docx 提取 DOCX 文本。"""
    try:
        from docx import Document
        doc = Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs).strip()
    except Exception:
        return ""


@tool_call("extract_text_from_file")
def extract_text_from_file(filename: str, content: bytes) -> str:
    """根据文件扩展名路由到对应的解析器。"""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return extract_text_from_pdf(content)
    elif lower.endswith(".docx") or lower.endswith(".doc"):
        return extract_text_from_docx(content)
    elif lower.endswith(".txt"):
        return content.decode("utf-8", errors="replace")
    else:
        raise ResumeParseError(
            f"Unsupported file type: {filename}",
            details={"supported": [".pdf", ".docx", ".doc", ".txt"]},
        )
