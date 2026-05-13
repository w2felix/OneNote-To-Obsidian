"""Presentation worker: PPTX native extraction + Vision AI for charts/figures."""

import logging
from io import BytesIO
from pathlib import Path

from vision_ai.workers.base import VisionWorker, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry
from vision_ai.vision_utils import pdf_to_images, encode_image, build_vision_message

logger = logging.getLogger(__name__)

PPTX_SUMMARY_PROMPT = """Based on this slide content extracted from a presentation, create a brief index card.

Slide content:
{slide_text}

Context:
- Page: {page_title}
- Section: {section_path}

Provide:
1. **Title & Author** (if identifiable)
2. **Topic Outline** — one line per major section/topic
3. **Key Takeaways** (3-5 bullets)
4. **Notable Figures/Data** mentioned

This is an INDEX CARD — not a slide-by-slide transcript."""

PDF_VISION_PROMPT = """Analyze these presentation slides and create a brief index card.

Context:
- Page: {page_title}
- Section: {section_path}

Provide:
1. **Title & Author** (if visible)
2. **Topic Outline** — one line per major topic covered
3. **Key Takeaways** (3-5 bullets)
4. **Notable Figures** — describe key charts/data shown

This is an INDEX CARD for quick reference — not a full transcript."""


class PresentationWorker(VisionWorker):

    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> str:
        filename = group.filenames[0]
        data = images[filename]
        ext = Path(filename).suffix.lower()

        if ext == '.pptx':
            return self._analyze_pptx(data, filename, page_context)
        else:
            return self._analyze_pdf_presentation(data, filename, page_context)

    def _analyze_pptx(self, data: bytes, filename: str, ctx: PageContext) -> str:
        text = self._extract_pptx_text(data)
        if not text:
            return ""

        truncated = text[:12000]
        if len(text) > 12000:
            truncated += "\n[... truncated]"

        prompt = PPTX_SUMMARY_PROMPT.format(
            slide_text=truncated,
            page_title=ctx.page_title,
            section_path=ctx.section_path,
        )

        messages = [{"role": "user", "content": prompt}]
        try:
            result = api_call_with_retry(messages, max_tokens=2048)
            return f"# Presentation Summary\n\n{result}"
        except Exception as e:
            logger.error(f"API call failed for presentation {filename}: {e}")
            return ""

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

    def _analyze_pdf_presentation(self, data: bytes, filename: str, ctx: PageContext) -> str:
        try:
            pil_images = pdf_to_images(data, dpi=150)
        except Exception as e:
            logger.error(f"Failed to render PDF {filename}: {e}")
            return ""

        if not pil_images:
            return ""

        # Sample slides: first, middle, last + a few others (max 8)
        total = len(pil_images)
        if total <= 8:
            sample_indices = list(range(total))
        else:
            step = total / 8
            sample_indices = [int(i * step) for i in range(8)]

        encoded = []
        for idx in sample_indices:
            encoded.append(encode_image(pil_images[idx], max_dim=1568))

        prompt = PDF_VISION_PROMPT.format(
            page_title=ctx.page_title,
            section_path=ctx.section_path,
        )
        prompt = f"These are {len(encoded)} sampled slides from a {total}-slide presentation.\n\n" + prompt

        messages = build_vision_message(encoded, prompt)
        try:
            result = api_call_with_retry(messages, max_tokens=2048)
            return f"# Presentation Summary ({total} slides)\n\n{result}"
        except Exception as e:
            logger.error(f"Vision API failed for {filename}: {e}")
            return ""
