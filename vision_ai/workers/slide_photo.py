"""Slide photo worker: batch parallel Vision AI for sequences of slide images."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from vision_ai.workers.base import VisionWorker, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry
from vision_ai.vision_utils import encode_image_bytes, build_vision_message
from vision_ai.ocr_utils import ocr_image_bytes

logger = logging.getLogger(__name__)

SLIDES_PER_BATCH = 5
MAX_CONCURRENT_BATCHES = 5

BATCH_PROMPT_TEMPLATE = """You are analyzing {n_slides} slide images from a presentation/talk.

Context:
- Page: {page_title}
- Section: {section_path}
{context_line}
{ocr_context}

For each slide, provide ONE line with the key point (what this slide communicates).
Format:
- Slide N: [key point — what data/concept/argument is shown]

Focus on the SUBSTANCE (data, findings, claims) not the visual format.
If a slide has charts/figures, describe what the data shows (axes, trends, key values).
Keep each line to 1-2 sentences maximum."""

SUMMARY_PROMPT_TEMPLATE = """Based on these slide-by-slide notes from a presentation, write a brief executive summary.

Slide notes:
{slide_notes}

Context:
- Page: {page_title}
- Section: {section_path}
{context_line}

Provide:
1. **Summary** (2-3 sentences): What is this talk/presentation about? What is the main message?
2. **Key Takeaways** (3-5 bullet points): The most important findings, claims, or decisions

Keep it concise. This is an index card, not a transcript."""


class SlidePhotoWorker(VisionWorker):

    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> str:
        filenames = group.filenames
        n_slides = len(filenames)

        # Pre-encode all images
        encoded = {}
        for fname in filenames:
            try:
                encoded[fname] = encode_image_bytes(images[fname], max_dim=1568)
            except Exception as e:
                logger.warning(f"Failed to encode {fname}: {e}")

        if not encoded:
            return ""

        # OCR pre-pass (quick, provides context for Vision)
        ocr_texts = {}
        for fname in filenames:
            if fname in images:
                ocr_texts[fname] = ocr_image_bytes(images[fname])

        # Batch processing
        batches = self._create_batches(list(encoded.keys()), SLIDES_PER_BATCH)
        slide_notes = self._process_batches(batches, encoded, ocr_texts, group, page_context)

        if not slide_notes:
            return ""

        # Generate executive summary
        summary = self._generate_summary(slide_notes, group, page_context)

        # Format final output
        return self._format_output(summary, slide_notes, n_slides, group)

    def _create_batches(self, filenames: list[str], batch_size: int) -> list[list[str]]:
        return [filenames[i:i + batch_size] for i in range(0, len(filenames), batch_size)]

    def _process_batches(self, batches: list[list[str]], encoded: dict[str, str],
                         ocr_texts: dict[str, str], group: AttachmentGroup,
                         ctx: PageContext) -> list[str]:
        all_notes = [''] * sum(len(b) for b in batches)

        context_line = f"- Speaker/topic: {group.context[:100]}" if group.context else ""

        def process_batch(batch_idx, batch_filenames):
            images_b64 = [encoded[f] for f in batch_filenames if f in encoded]
            if not images_b64:
                return batch_idx, ""

            # Build OCR context
            ocr_lines = []
            for f in batch_filenames:
                ocr = ocr_texts.get(f, "")
                if ocr:
                    ocr_lines.append(f"[OCR hint for slide: {ocr[:150]}]")
            ocr_context = "\n".join(ocr_lines) if ocr_lines else ""

            start_num = batch_idx * SLIDES_PER_BATCH + 1
            n = len(images_b64)
            prompt = BATCH_PROMPT_TEMPLATE.format(
                n_slides=n,
                page_title=ctx.page_title,
                section_path=ctx.section_path,
                context_line=context_line,
                ocr_context=f"OCR hints:\n{ocr_context}" if ocr_context else "",
            )
            # Prepend slide numbering instruction
            prompt = f"These are slides {start_num}-{start_num + n - 1}.\n\n" + prompt

            messages = build_vision_message(images_b64, prompt)
            try:
                return batch_idx, api_call_with_retry(messages, max_tokens=2048)
            except Exception as e:
                logger.error(f"Batch {batch_idx} failed: {e}")
                return batch_idx, ""

        # Parallel execution
        results = {}
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BATCHES) as executor:
            futures = {
                executor.submit(process_batch, i, batch): i
                for i, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                batch_idx, result = future.result()
                results[batch_idx] = result

        # Assemble in order
        ordered_notes = [results.get(i, "") for i in range(len(batches))]
        return [n for n in ordered_notes if n]

    def _generate_summary(self, slide_notes: list[str], group: AttachmentGroup,
                          ctx: PageContext) -> str:
        all_notes_text = '\n\n'.join(slide_notes)
        # Truncate if too long
        if len(all_notes_text) > 10000:
            all_notes_text = all_notes_text[:10000] + "\n[... truncated]"

        context_line = f"- Speaker/topic: {group.context[:100]}" if group.context else ""

        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            slide_notes=all_notes_text,
            page_title=ctx.page_title,
            section_path=ctx.section_path,
            context_line=context_line,
        )

        messages = [{"role": "user", "content": prompt}]
        try:
            return api_call_with_retry(messages, max_tokens=1024)
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return ""

    def _format_output(self, summary: str, slide_notes: list[str],
                       n_slides: int, group: AttachmentGroup) -> str:
        lines = []

        # Title
        title_parts = ["Slide Analysis"]
        if group.context:
            title_parts = [group.context.split('\n')[0].strip()]
        lines.append(f"# {' — '.join(title_parts)} ({n_slides} slides)")
        lines.append("")

        # Summary
        if summary:
            lines.append(summary)
            lines.append("")

        # Slide-by-slide notes
        lines.append("## Slide Notes")
        lines.append("")
        for note in slide_notes:
            lines.append(note)
            lines.append("")

        return '\n'.join(lines)
