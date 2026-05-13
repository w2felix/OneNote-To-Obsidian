"""Diagram worker: structural extraction for org charts, flowcharts, pipeline tables."""

import logging

from vision_ai.workers.base import VisionWorker, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry
from vision_ai.vision_utils import encode_image_bytes, build_vision_message

logger = logging.getLogger(__name__)


PROMPT_TEMPLATE = """Analyze this diagram/chart and extract its structure for a knowledge base index card.

Context from the page where this image is embedded:
- Page title: {page_title}
- Section: {section_path}
{context_line}

Provide a structured extraction:

1. **Diagram type** (org chart, flowchart, pipeline table, process diagram, etc.)
2. **Structure** — Extract the hierarchy or relationships as a nested list:
   - For org charts: names, roles, reporting lines
   - For flowcharts: steps, decisions, connections
   - For tables: column headers and key data
   - For pipeline views: stages, items in each stage
3. **Key entities** — List the most important names, roles, or items

Format as clean markdown. Focus on STRUCTURE and RELATIONSHIPS, not visual styling.
Keep it concise — this is an index card."""


class DiagramWorker(VisionWorker):

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
            img_b64 = encode_image_bytes(data, max_dim=2048)
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
            return api_call_with_retry(messages, max_tokens=2048)
        except Exception as e:
            logger.error(f"Vision API failed for {filename}: {e}")
            return ""
