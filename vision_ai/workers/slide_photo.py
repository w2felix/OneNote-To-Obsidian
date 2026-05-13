"""Slide photo worker: Vision AI analysis for sequences of slide images."""

import logging
from typing import Optional

from vision_ai.workers.base import VisionWorker, AnalysisResult, parse_structured_response, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry
from vision_ai.vision_utils import encode_image_bytes, build_vision_message

logger = logging.getLogger(__name__)

MAX_SAMPLE_SLIDES = 8

PROMPT_TEMPLATE = """Analyze these {n_slides} slide images (sampled from {total} total slides in a presentation/talk).

Context:
- Page: {page_title}
- Section: {section_path}
{context_line}

Respond in EXACTLY this format:

TITLE: [presentation/talk title if visible]
AUTHORS: [speaker/presenter name if visible, or "Unknown"]
DATE: [date if visible, or "Unknown"]
KEY_POINTS:
- [key finding/message/claim 1]
- [key finding/message/claim 2]
- [key finding/message/claim 3]
- [key finding/message/claim 4]
- [key finding/message/claim 5]
BODY:
[2-3 sentence executive summary: what is this talk about and what is the main message? Then briefly note any key charts/figures and what data they show.]"""


class SlidePhotoWorker(VisionWorker):

    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> Optional[AnalysisResult]:
        filenames = group.filenames
        total = len(filenames)

        # Sample up to MAX_SAMPLE_SLIDES evenly distributed
        if total <= MAX_SAMPLE_SLIDES:
            sample_filenames = filenames
        else:
            step = total / MAX_SAMPLE_SLIDES
            sample_filenames = [filenames[int(i * step)] for i in range(MAX_SAMPLE_SLIDES)]

        # Encode sampled slides
        encoded = []
        for fname in sample_filenames:
            try:
                encoded.append(encode_image_bytes(images[fname], max_dim=1568))
            except Exception as e:
                logger.warning(f"Failed to encode {fname}: {e}")

        if not encoded:
            return None

        context_line = f"- Speaker/topic: {group.context[:100]}" if group.context else ""

        prompt = PROMPT_TEMPLATE.format(
            n_slides=len(encoded),
            total=total,
            page_title=page_context.page_title,
            section_path=page_context.section_path,
            context_line=context_line,
        )

        messages = build_vision_message(encoded, prompt)
        try:
            response = api_call_with_retry(messages, max_tokens=2048)
            result = parse_structured_response(response, default_content_type='slide_photos')
            result.extra['slide_count'] = total
            return result
        except Exception as e:
            logger.error(f"Vision API failed for slide group: {e}")
            return None
