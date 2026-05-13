"""Screenshot worker: general fallback for single images (video calls, UI captures, misc)."""

import logging

from vision_ai.workers.base import VisionWorker, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry
from vision_ai.vision_utils import encode_image_bytes, build_vision_message

logger = logging.getLogger(__name__)


PROMPT_TEMPLATE = """Describe this image concisely for a knowledge base index card.

Context from the page where this image is embedded:
- Page title: {page_title}
- Section: {section_path}
{context_line}

Provide:
1. A 2-3 sentence description of what the image shows
2. Any readable text content (key points only, not verbatim transcription)
3. Notable elements (people mentioned, data shown, tools/platforms visible)

Format as markdown. Keep it brief — this is an index card, not a full transcription."""


class ScreenshotWorker(VisionWorker):

    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> str:
        results = []
        for filename in group.filenames:
            data = images.get(filename)
            if not data:
                continue
            result = self._analyze_single(filename, data, group, page_context)
            if result:
                results.append(result)

        return '\n\n---\n\n'.join(results) if results else ""

    def _analyze_single(self, filename: str, data: bytes, group: AttachmentGroup,
                        ctx: PageContext) -> str:
        try:
            img_b64 = encode_image_bytes(data, max_dim=1568)
        except Exception as e:
            logger.warning(f"Failed to encode {filename}: {e}")
            return ""

        context_line = f"- Surrounding notes: {group.context[:200]}" if group.context else ""

        prompt = PROMPT_TEMPLATE.format(
            page_title=ctx.page_title,
            section_path=ctx.section_path,
            context_line=context_line,
        )

        messages = build_vision_message([img_b64], prompt)

        try:
            return api_call_with_retry(messages, max_tokens=1024)
        except Exception as e:
            logger.error(f"Vision API failed for {filename}: {e}")
            return ""
