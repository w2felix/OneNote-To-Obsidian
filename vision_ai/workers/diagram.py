"""Diagram worker: structural extraction for org charts, flowcharts, pipeline tables."""

import logging
from typing import Optional

from vision_ai.workers.base import VisionWorker, AnalysisResult, parse_structured_response, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry
from vision_ai.vision_utils import encode_image_bytes, build_vision_message

logger = logging.getLogger(__name__)


PROMPT_TEMPLATE = """Analyze this diagram/chart and extract its structure.

Context:
- Page title: {page_title}
- Section: {section_path}
{context_line}

Respond in EXACTLY this format:

TITLE: [diagram title or subject]
CONTENT_TYPE: [org chart, flowchart, pipeline table, process diagram, network diagram, etc.]
KEY_POINTS:
- [key relationship/entity 1]
- [key relationship/entity 2]
- [key relationship/entity 3]
BODY:
[Extract the hierarchy or relationships as a nested markdown list. For org charts: names, roles, reporting lines. For flowcharts: steps and connections. For tables: column headers and key data.]"""


class DiagramWorker(VisionWorker):

    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> Optional[AnalysisResult]:
        results = []
        for filename in group.filenames:
            data = images.get(filename)
            if not data:
                continue
            result = self._analyze_single(filename, data, group, page_context)
            if result:
                results.append(result)

        if not results:
            return None

        if len(results) == 1:
            return results[0]

        # Merge multiple diagrams into one result
        merged = AnalysisResult(
            title=results[0].title,
            content_type=results[0].content_type,
            key_points=[],
            body="",
        )
        bodies = []
        for r in results:
            merged.key_points.extend(r.key_points)
            if r.body:
                bodies.append(r.body)
        merged.body = '\n\n---\n\n'.join(bodies)
        merged.extra['diagram_count'] = len(results)
        return merged

    def _analyze_single(self, filename: str, data: bytes, group: AttachmentGroup,
                        ctx: PageContext) -> Optional[AnalysisResult]:
        try:
            img_b64 = encode_image_bytes(data, max_dim=2048)
        except Exception as e:
            logger.warning(f"Failed to encode {filename}: {e}")
            return None

        context_line = f"- Surrounding notes: {group.context[:200]}" if group.context else ""

        prompt = PROMPT_TEMPLATE.format(
            page_title=ctx.page_title,
            section_path=ctx.section_path,
            context_line=context_line,
        )

        messages = build_vision_message([img_b64], prompt)

        try:
            response = api_call_with_retry(messages, max_tokens=2048)
            return parse_structured_response(response, default_content_type='diagram')
        except Exception as e:
            logger.error(f"Vision API failed for {filename}: {e}")
            return None
