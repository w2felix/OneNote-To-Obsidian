"""Presentation worker: PPTX native extraction + Vision AI for charts/figures."""

import logging
from io import BytesIO
from pathlib import Path
from typing import Optional

from vision_ai.workers.base import VisionWorker, AnalysisResult, parse_structured_response, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry
from vision_ai.vision_utils import pdf_to_images, pdf_page_count, encode_image, build_vision_message

logger = logging.getLogger(__name__)

PPTX_PROMPT = """Analyze this slide content extracted from a presentation.

Slide content:
{slide_text}

Context:
- Page: {page_title}
- Section: {section_path}

Respond in EXACTLY this format (fill in each field):

TITLE: [presentation title]
AUTHORS: [comma-separated list, or "Unknown"]
DATE: [date if mentioned, or "Unknown"]
KEY_POINTS:
- [point 1]
- [point 2]
- [point 3]
BODY:
[2-3 sentence summary of what this presentation covers and its main message]"""

PDF_VISION_PROMPT = """Analyze these presentation slides.

Context:
- Page: {page_title}
- Section: {section_path}

Respond in EXACTLY this format (fill in each field):

TITLE: [presentation title visible on slides]
AUTHORS: [comma-separated list, or "Unknown"]
DATE: [date if visible, or "Unknown"]
KEY_POINTS:
- [key finding/message 1]
- [key finding/message 2]
- [key finding/message 3]
BODY:
[2-3 sentence summary of what these slides cover and the main takeaway]"""


class PresentationWorker(VisionWorker):

    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> Optional[AnalysisResult]:
        filename = group.filenames[0]
        data = images[filename]
        ext = Path(filename).suffix.lower()

        if ext == '.pptx':
            return self._analyze_pptx(data, filename, page_context)
        else:
            return self._analyze_pdf_presentation(data, filename, page_context)

    def _analyze_pptx(self, data: bytes, filename: str, ctx: PageContext) -> Optional[AnalysisResult]:
        text = self._extract_pptx_text(data)
        if not text:
            return None

        slide_count = text.count('--- Slide ')
        truncated = text[:12000]
        if len(text) > 12000:
            truncated += "\n[... truncated]"

        prompt = PPTX_PROMPT.format(
            slide_text=truncated,
            page_title=ctx.page_title,
            section_path=ctx.section_path,
        )

        messages = [{"role": "user", "content": prompt}]
        try:
            response = api_call_with_retry(messages, max_tokens=2048)
            result = parse_structured_response(response, default_content_type='presentation')
            result.extra['slide_count'] = slide_count
            return result
        except Exception as e:
            logger.error(f"API call failed for presentation {filename}: {e}")
            return None

    def _extract_pptx_text(self, data: bytes) -> str:
        try:
            from pptx import Presentation
            prs = Presentation(BytesIO(data))
            slides_text = []
            for i, slide in enumerate(prs.slides, 1):
                texts = []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            text = para.text.strip()
                            if text:
                                texts.append(text)
                if texts:
                    slides_text.append(f"--- Slide {i} ---\n" + '\n'.join(texts))
            return '\n\n'.join(slides_text)
        except Exception as e:
            logger.warning(f"python-pptx extraction failed: {e}")
            return ""

    def _analyze_pdf_presentation(self, data: bytes, filename: str,
                                  ctx: PageContext) -> Optional[AnalysisResult]:
        try:
            total = pdf_page_count(data)
        except Exception as e:
            logger.error(f"Failed to read PDF {filename}: {e}")
            return None

        if total == 0:
            return None

        if total <= 8:
            sample_indices = list(range(total))
        else:
            step = total / 8
            sample_indices = [int(i * step) for i in range(8)]

        try:
            pil_images = pdf_to_images(data, dpi=150, page_indices=sample_indices)
        except Exception as e:
            logger.error(f"Failed to render PDF {filename}: {e}")
            return None

        encoded = []
        for img in pil_images:
            encoded.append(encode_image(img, max_dim=1568))

        prompt = f"These are {len(encoded)} sampled slides from a {total}-slide presentation.\n\n"
        prompt += PDF_VISION_PROMPT.format(
            page_title=ctx.page_title,
            section_path=ctx.section_path,
        )

        messages = build_vision_message(encoded, prompt)
        try:
            response = api_call_with_retry(messages, max_tokens=2048)
            result = parse_structured_response(response, default_content_type='presentation')
            result.extra['slide_count'] = total
            return result
        except Exception as e:
            logger.error(f"Vision API failed for {filename}: {e}")
            return None
