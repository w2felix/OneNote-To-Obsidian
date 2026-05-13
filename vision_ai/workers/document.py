"""Document worker: structural summary for text-heavy files (PDFs, DOCX, HTML)."""

import logging
from pathlib import Path
from typing import Optional

from vision_ai.workers.base import VisionWorker, AnalysisResult, parse_structured_response, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry
from vision_ai.vision_utils import pdf_to_images, encode_image, build_vision_message

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 15000

PROMPT_TEMPLATE = """Analyze this document and create a structured index card.

Context:
- Page: {page_title}
- Section: {section_path}

Extracted text (first pages):
{extracted_text}

Respond in EXACTLY this format (fill in each field):

TITLE: [document title/subject]
CONTENT_TYPE: [contract, report, paper, memo, protocol, manuscript, etc.]
AUTHORS: [comma-separated, or "Unknown"]
DATE: [publication/execution date, or "Unknown"]
KEY_POINTS:
- [most important provision/finding/conclusion 1]
- [key point 2]
- [key point 3]
BODY:
[Brief structural overview: main sections and what each covers. Enable a reader to quickly find specific information within the original document.]"""


FALLBACK_VISION_PROMPT = """Analyze this document page and extract its structure.

Respond in EXACTLY this format:

TITLE: [document title]
CONTENT_TYPE: [document type]
AUTHORS: [if visible, or "Unknown"]
DATE: [if visible, or "Unknown"]
KEY_POINTS:
- [key point 1]
- [key point 2]
BODY:
[Main sections visible and key information]"""


class DocumentWorker(VisionWorker):

    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> Optional[AnalysisResult]:
        filename = group.filenames[0]
        data = images[filename]
        ext = Path(filename).suffix.lower()

        text = self._extract_text(data, ext)

        if text and len(text) > 100:
            return self._analyze_with_text(text, filename, page_context)
        elif ext == '.pdf':
            return self._analyze_with_vision(data, filename, page_context)
        else:
            logger.warning(f"No text extracted from {filename}, skipping")
            return None

    def _extract_text(self, data: bytes, ext: str) -> str:
        if ext == '.pdf':
            return self._extract_pdf_text(data)
        elif ext == '.docx':
            return self._extract_docx_text(data)
        elif ext == '.html':
            return self._extract_html_text(data)
        return ""

    def _extract_pdf_text(self, pdf_bytes: bytes) -> str:
        try:
            import pdfplumber
            from io import BytesIO
            text_parts = []
            with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages[:20]:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            return '\n\n'.join(text_parts)
        except Exception as e:
            logger.debug(f"pdfplumber extraction failed: {e}")
            return ""

    def _extract_docx_text(self, data: bytes) -> str:
        try:
            from docx import Document
            from io import BytesIO
            doc = Document(BytesIO(data))
            return '\n\n'.join(para.text for para in doc.paragraphs if para.text.strip())
        except Exception as e:
            logger.debug(f"docx extraction failed: {e}")
            return ""

    def _extract_html_text(self, data: bytes) -> str:
        try:
            import re
            text = data.decode('utf-8', errors='replace')
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text
        except Exception as e:
            logger.debug(f"HTML extraction failed: {e}")
            return ""

    def _analyze_with_text(self, text: str, filename: str,
                           ctx: PageContext) -> Optional[AnalysisResult]:
        truncated = text[:MAX_TEXT_CHARS]
        if len(text) > MAX_TEXT_CHARS:
            truncated += "\n[... truncated]"

        prompt = PROMPT_TEMPLATE.format(
            page_title=ctx.page_title,
            section_path=ctx.section_path,
            extracted_text=truncated,
        )

        messages = [{"role": "user", "content": prompt}]
        try:
            response = api_call_with_retry(messages, max_tokens=2048)
            return parse_structured_response(response)
        except Exception as e:
            logger.error(f"API call failed for document {filename}: {e}")
            return None

    def _analyze_with_vision(self, pdf_bytes: bytes, filename: str,
                             ctx: PageContext) -> Optional[AnalysisResult]:
        """Fallback: use Vision AI on first few pages."""
        try:
            pil_images = pdf_to_images(pdf_bytes, dpi=150, max_pages=3)
        except Exception:
            return None

        encoded = [encode_image(img, max_dim=1568) for img in pil_images[:3]]
        if not encoded:
            return None

        messages = build_vision_message(encoded, FALLBACK_VISION_PROMPT)
        try:
            response = api_call_with_retry(messages, max_tokens=2048)
            return parse_structured_response(response)
        except Exception as e:
            logger.error(f"Vision fallback failed for {filename}: {e}")
            return None
