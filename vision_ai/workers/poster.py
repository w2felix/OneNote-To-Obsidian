"""Poster worker: scientific poster PDF analysis with Vision AI."""

import logging

from vision_ai.workers.base import VisionWorker, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry
from vision_ai.vision_utils import pdf_to_images, encode_image, build_vision_message
from vision_ai.ocr_utils import ocr_image

logger = logging.getLogger(__name__)

POSTER_DPI = 300

PROMPT_TEMPLATE = """Analyze this scientific poster and create a structured index card.

Context:
- Page: {page_title}
- Section: {section_path}
{ocr_context}

Extract the following (keep each section concise):

1. **Title** — exact title of the poster
2. **Authors & Affiliations** — names and institutions
3. **Key Findings** (3-5 bullet points) — the main results/conclusions
4. **Figures** — for each figure/chart: one line describing what it shows and the key takeaway
5. **Methods** (1-2 sentences) — brief approach used
6. **Conclusions** (1-2 sentences)

This is an INDEX CARD for search and skimming — not a full transcription.
Focus on findings and data, not background."""


class PosterWorker(VisionWorker):

    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> str:
        filename = group.filenames[0]
        data = images[filename]

        # Render poster at high DPI
        try:
            pil_images = pdf_to_images(data, dpi=POSTER_DPI)
        except Exception as e:
            logger.error(f"Failed to render poster {filename}: {e}")
            return ""

        if not pil_images:
            return ""

        # Encode all pages (posters can be 1-2 pages)
        encoded = []
        for img in pil_images[:3]:
            encoded.append(encode_image(img, max_dim=2048))

        # OCR pre-pass for RAG context
        ocr_texts = []
        for img in pil_images[:2]:
            ocr = ocr_image(img)
            if ocr:
                ocr_texts.append(ocr[:2000])

        ocr_context = ""
        if ocr_texts:
            combined_ocr = '\n---\n'.join(ocr_texts)[:4000]
            ocr_context = f"OCR-extracted text (may have errors, use for reference):\n{combined_ocr}"

        prompt = PROMPT_TEMPLATE.format(
            page_title=page_context.page_title,
            section_path=page_context.section_path,
            ocr_context=ocr_context,
        )

        messages = build_vision_message(encoded, prompt)

        try:
            result = api_call_with_retry(messages, max_tokens=3072)
            return f"# Poster Analysis\n\n{result}"
        except Exception as e:
            logger.error(f"Vision API failed for poster {filename}: {e}")
            return ""
