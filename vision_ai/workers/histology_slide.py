"""Histology slide worker: IHC, IF, H&E, and tissue staining images."""

import logging
from typing import Optional

from vision_ai.workers.base import VisionWorker, AnalysisResult, parse_structured_response, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry
from vision_ai.vision_utils import encode_image_bytes, build_vision_message

logger = logging.getLogger(__name__)


PROMPT_TEMPLATE = """Analyze this histology/pathology image.

Context:
- Page title: {page_title}
- Section: {section_path}
{context_line}

Respond in EXACTLY this format:

TITLE: [tissue type and stain, e.g. "Lung adenocarcinoma - PD-L1 IHC"]
CONTENT_TYPE: [IHC, H&E, IF/multiplex, special stain, etc.]
KEY_POINTS:
- [staining pattern: intensity, percentage positive, localization]
- [tissue morphology observation]
- [notable finding]
BODY:
[Brief description of the antibody/marker if identifiable, tissue type, and any notable observations like heterogeneity, necrosis, stromal vs tumor staining.]"""


class HistologySlideWorker(VisionWorker):

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
        merged.extra['image_count'] = len(results)
        return merged

    def _analyze_single(self, filename: str, data: bytes, group: AttachmentGroup,
                        ctx: PageContext) -> Optional[AnalysisResult]:
        try:
            img_b64 = encode_image_bytes(data, max_dim=1568)
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
            response = api_call_with_retry(messages, max_tokens=1024)
            return parse_structured_response(response, default_content_type='histology')
        except Exception as e:
            logger.error(f"Vision API failed for {filename}: {e}")
            return None
