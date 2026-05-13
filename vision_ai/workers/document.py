"""Document worker: structural summary for text-heavy files (PDFs, DOCX, HTML)."""

import logging
from pathlib import Path

from vision_ai.workers.base import VisionWorker, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry
from vision_ai.vision_utils import pdf_to_images, encode_image, build_vision_message

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 15000

PROMPT_TEMPLATE = """Analyze this document and create a structural index card for a knowledge base.

Context:
- Page: {page_title}
- Section: {section_path}

Extracted text (first pages):
{extracted_text}

Provide:
1. **Document Type** (contract, report, paper, memo, etc.)
2. **Title/Subject**
3. **Parties/Authors** (who is involved)
4. **Date** (execution date, publication date)
5. **Structure** — list main sections/articles with one-line descriptions
6. **Key Terms** (3-5 bullets) — the most important provisions, findings, or conclusions
7. **Key Dates/Deadlines** if applicable

This is a TABLE OF CONTENTS + KEY METADATA card — NOT a full extraction.
Enable a reader (or AI) to quickly find specific information within the original document."""


FALLBACK_VISION_PROMPT = """Analyze this document page and extract its structure.

Provide:
1. Document type and title
2. Key parties or authors
3. Main sections visible
4. Any key dates, terms, or obligations visible

Keep it concise — this is an index card."""


class DocumentWorker(VisionWorker):

    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> str:
        filename = group.filenames[0]
        data = images[filename]
        ext = Path(filename).suffix.lower()

        # Try text extraction first
        text = self._extract_text(data, ext)

        if text and len(text) > 100:
            return self._analyze_with_text(text, filename, page_context)
        elif ext == '.pdf':
            return self._analyze_with_vision(data, filename, page_context)
        else:
            # Non-PDF with no extractable text — nothing to analyze
            logger.warning(f"No text extracted from {filename}, skipping")
            return ""

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

    def _analyze_with_text(self, text: str, filename: str, ctx: PageContext) -> str:
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
            result = api_call_with_retry(messages, max_tokens=2048)
            return f"# Document Summary\n\n{result}"
        except Exception as e:
            logger.error(f"API call failed for document {filename}: {e}")
            return ""

    def _analyze_with_vision(self, pdf_bytes: bytes, filename: str, ctx: PageContext) -> str:
        """Fallback: use Vision AI on first few pages."""
        try:
            pil_images = pdf_to_images(pdf_bytes, dpi=150)
        except Exception:
            return ""

        # Send first 3 pages
        encoded = [encode_image(img, max_dim=1568) for img in pil_images[:3]]
        if not encoded:
            return ""

        messages = build_vision_message(encoded, FALLBACK_VISION_PROMPT)
        try:
            result = api_call_with_retry(messages, max_tokens=2048)
            return f"# Document Summary\n\n{result}"
        except Exception as e:
            logger.error(f"Vision fallback failed for {filename}: {e}")
            return ""
