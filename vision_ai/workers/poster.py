"""Poster worker: scientific poster PDF analysis with Vision AI."""

import logging
from typing import Optional

from vision_ai.workers.base import VisionWorker, AnalysisResult, parse_structured_response, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry
from vision_ai.vision_utils import pdf_to_images, encode_image, build_vision_message
from vision_ai.ocr_utils import ocr_image

logger = logging.getLogger(__name__)

POSTER_DPI = 300

PROMPT_TEMPLATE = """Analyze this scientific poster.

Context:
- Page: {page_title}
- Section: {section_path}
{ocr_context}

Respond in EXACTLY this format (fill in each field):

TITLE: [exact title of the poster]
AUTHORS: [comma-separated names with affiliations in parentheses, or "Unknown"]
DATE: [year/date if visible, or "Unknown"]
KEY_POINTS:
- [main finding/result 1]
- [main finding/result 2]
- [main finding/result 3]
METHODS: [1-2 sentence methods summary]
BODY:
[Brief description of figures/charts shown and their key takeaways. Focus on data and conclusions.]"""


class PosterWorker(VisionWorker):

    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> Optional[AnalysisResult]:
        filename = group.filenames[0]
        data = images[filename]

        try:
            pil_images = pdf_to_images(data, dpi=POSTER_DPI, max_pages=3)
        except Exception as e:
            logger.error(f"Failed to render poster {filename}: {e}")
            return None

        if not pil_images:
            return None

        encoded = []
        for img in pil_images[:3]:
            encoded.append(encode_image(img, max_dim=2048))

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
            response = api_call_with_retry(messages, max_tokens=3072)
            return parse_structured_response(response, default_content_type='poster')
        except Exception as e:
            logger.error(f"Vision API failed for poster {filename}: {e}")
            return None
