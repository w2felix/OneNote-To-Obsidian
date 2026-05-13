"""Content-type detection using file heuristics, page context, and image grouping."""

import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LANDSCAPE_THRESHOLD = 1.2
ASPECT_RATIO_TOLERANCE = 0.25
MIN_GROUP_SIZE = 5
TEXT_DENSE_THRESHOLD = 50  # chars per page


@dataclass
class PageContext:
    page_title: str = ""
    page_text: str = ""
    section_path: str = ""
    parent_page: Optional[str] = None
    embed_order: list = field(default_factory=list)
    # embed_order: list of {'type': 'image'|'file'|'text', 'filename': str, 'text': str}


@dataclass
class AttachmentGroup:
    worker_type: str  # slide_photo, poster, document, presentation, diagram, tabular_data, screenshot, histology_slide
    filenames: list[str] = field(default_factory=list)
    context: str = ""  # speaker name, topic, etc.
    confidence: float = 1.0


# --- Extension-based routing (Tier 1) ---

EXTENSION_MAP = {
    '.pptx': 'presentation',
    '.docx': 'document',
    '.html': 'document',
    '.xlsx': 'tabular_data',
    '.csv': 'tabular_data',
    '.tsv': 'tabular_data',
    '.gif': 'skip',
}

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif'}

_CONTEXT_KEYWORD_PATTERNS = {
    'poster': [r'\bposter\b', r'\babstract #', r'\bposter session\b'],
    'document': [r'\bagreement\b', r'\bcontract\b', r'\blicense\b', r'\bprotocol\b',
                 r'\breport\b', r'\bmanuscript\b'],
    'presentation': [r'\bpresentation\b', r'\bslides\b', r'\bdeck\b', r'\bkickoff\b', r'\bkick-off\b'],
    'diagram': [r'\bstructure\b', r'\borg chart\b', r'\borganization\b', r'\bpipeline\b',
                r'\bteam\b', r'\bhierarchy\b', r'\bhead of\b', r'\blead:', r'\bdirector\b',
                r'\bvp\b', r'\breporting\b'],
    'histology_slide': [r'\bihc\b', r'\bimmunohisto', r'\bh&e\b', r'\bh\s*&\s*e\b',
                        r'\bstaining\b', r'\bhistolog', r'\bpatholog', r'\btissue\s+micro',
                        r'\btma\b', r'\bimmunofluo', r'\bfluorescence\b', r'\bmultiplex\b',
                        r'\bbiopsy\b', r'\bsection\s+stain', r'\bcell\s+stain'],
}

# Pre-compile all keyword patterns for performance
CONTEXT_KEYWORDS: dict[str, list[re.Pattern]] = {
    category: [re.compile(p, re.IGNORECASE) for p in patterns]
    for category, patterns in _CONTEXT_KEYWORD_PATTERNS.items()
}


def _keyword_match(pattern: re.Pattern, text: str) -> bool:
    """Check if a pre-compiled keyword pattern matches in text."""
    return pattern.search(text) is not None


def detect_content_types(attachments: dict[str, bytes], page_context: PageContext) -> list[AttachmentGroup]:
    """Classify all attachments on a page into typed groups for processing."""
    groups = []
    images_to_group = []

    for filename, data in attachments.items():
        ext = Path(filename).suffix.lower()

        # Tier 1: extension-based
        if ext in EXTENSION_MAP:
            worker = EXTENSION_MAP[ext]
            if worker != 'skip':
                groups.append(AttachmentGroup(worker_type=worker, filenames=[filename]))
            continue

        if ext == '.pdf':
            worker = _classify_pdf(data, filename, page_context)
            groups.append(AttachmentGroup(worker_type=worker, filenames=[filename]))
            continue

        if ext in IMAGE_EXTENSIONS:
            images_to_group.append(filename)
            continue

        # Unknown extension — skip
        logger.debug(f"Skipping unknown file type: {filename}")

    # Group images using page embed order and aspect ratios
    if images_to_group:
        image_groups = _group_images(images_to_group, attachments, page_context)
        groups.extend(image_groups)

    return groups


def _classify_pdf(data: bytes, filename: str, ctx: PageContext) -> str:
    """Tier 2: Classify PDF by metadata + page context."""
    try:
        from vision_ai.vision_utils import pdf_metadata
        meta = pdf_metadata(data)
    except Exception:
        return 'document'

    context_text = f"{ctx.page_title} {ctx.page_text} {ctx.section_path}".lower()
    filename_lower = filename.lower()

    # Context keyword boost
    for pattern in CONTEXT_KEYWORDS['poster']:
        if _keyword_match(pattern, context_text) or _keyword_match(pattern, filename_lower):
            if meta['page_count'] <= 2:
                return 'poster'

    for pattern in CONTEXT_KEYWORDS['document']:
        if _keyword_match(pattern, context_text) or _keyword_match(pattern, filename_lower):
            return 'document'

    for pattern in CONTEXT_KEYWORDS['presentation']:
        if _keyword_match(pattern, context_text) or _keyword_match(pattern, filename_lower):
            return 'presentation'

    # Heuristic rules
    if meta['page_count'] == 1:
        if meta['is_landscape'] and meta['file_size'] > 2_000_000:
            return 'poster'
        if meta['chars_per_page'] > TEXT_DENSE_THRESHOLD:
            return 'document'
        return 'poster' if meta['is_landscape'] else 'document'

    # Multi-page
    if meta['is_landscape']:
        if meta['chars_per_page'] > TEXT_DENSE_THRESHOLD:
            return 'document'
        return 'presentation'

    if meta['chars_per_page'] > TEXT_DENSE_THRESHOLD:
        return 'document'

    return 'presentation'


def _group_images(image_filenames: list[str], attachments: dict[str, bytes],
                  ctx: PageContext) -> list[AttachmentGroup]:
    """Group images by embed order, aspect ratio similarity, and text separators."""
    from vision_ai.vision_utils import image_dimensions

    # Build ordered sequence from page embed order
    ordered_images = _order_by_embed_sequence(image_filenames, ctx)

    # Get dimensions for each image
    dimensions = {}
    for fname in ordered_images:
        try:
            dimensions[fname] = image_dimensions(attachments[fname])
        except Exception:
            dimensions[fname] = (1, 1)

    # Split into groups by text separators and aspect ratio
    groups = []
    current_group = []
    current_context = _get_preceding_text(ordered_images[0], ctx) if ordered_images else ""

    for i, fname in enumerate(ordered_images):
        w, h = dimensions[fname]
        aspect = w / max(h, 1)

        # Check for text separator before this image
        if i > 0:
            text_between = _get_text_between(ordered_images[i - 1], fname, ctx)
            if text_between and _is_speaker_separator(text_between):
                # Close current group
                if current_group:
                    groups.append(_finalize_group(current_group, dimensions, current_context, ctx))
                current_group = []
                current_context = text_between.strip()

        # Check aspect ratio compatibility with current group
        if current_group:
            first_w, first_h = dimensions[current_group[0]]
            first_aspect = first_w / max(first_h, 1)
            if abs(aspect - first_aspect) / max(first_aspect, 0.1) > ASPECT_RATIO_TOLERANCE:
                # Incompatible — close group
                if current_group:
                    groups.append(_finalize_group(current_group, dimensions, current_context, ctx))
                current_group = []
                current_context = _get_preceding_text(fname, ctx)

        current_group.append(fname)

    # Final group
    if current_group:
        groups.append(_finalize_group(current_group, dimensions, current_context, ctx))

    # Merge pass: combine consecutive small groups of the same worker type
    groups = _merge_small_groups(groups)

    return groups


def _merge_small_groups(groups: list[AttachmentGroup]) -> list[AttachmentGroup]:
    """Merge consecutive small groups of the same worker type to reduce fragmentation."""
    if len(groups) <= 1:
        return groups

    merged = [groups[0]]
    mergeable_types = {'screenshot', 'diagram', 'histology_slide'}
    for group in groups[1:]:
        prev = merged[-1]
        both_small = len(prev.filenames) < MIN_GROUP_SIZE and len(group.filenames) < MIN_GROUP_SIZE
        # Merge same-type groups, or merge different types within the mergeable set
        same_type = prev.worker_type == group.worker_type
        cross_mergeable = (prev.worker_type in mergeable_types
                           and group.worker_type in mergeable_types)
        compatible = same_type or cross_mergeable

        if compatible and both_small and len(prev.filenames) + len(group.filenames) <= MIN_GROUP_SIZE * 2:
            # Merge into previous group; keep prev's worker_type unless cross-merging
            prev.filenames.extend(group.filenames)
            if not same_type:
                prev.worker_type = 'screenshot'  # generic fallback for mixed groups
            if not prev.context and group.context:
                prev.context = group.context
        else:
            merged.append(group)

    return merged


def _finalize_group(filenames: list[str], dimensions: dict, context: str,
                    page_ctx: PageContext) -> AttachmentGroup:
    """Determine worker type for a group based on size and context."""
    # Check if page-level context suggests poster (applies to all group sizes)
    context_text = f"{context} {page_ctx.page_title} {page_ctx.section_path}".lower()
    for pattern in CONTEXT_KEYWORDS['poster']:
        if _keyword_match(pattern, context_text):
            return AttachmentGroup(
                worker_type='poster',
                filenames=filenames,
                context=context,
            )

    if len(filenames) >= MIN_GROUP_SIZE:
        return AttachmentGroup(
            worker_type='slide_photo',
            filenames=filenames,
            context=context,
        )

    # Single or pair of images — classify individually
    if len(filenames) == 1:
        worker = _classify_single_image(filenames[0], dimensions, context, page_ctx)
        return AttachmentGroup(worker_type=worker, filenames=filenames, context=context)

    # 2-4 images — could be a small slide group or individual
    w, h = dimensions.get(filenames[0], (1, 1))
    if w / max(h, 1) > LANDSCAPE_THRESHOLD:
        return AttachmentGroup(worker_type='slide_photo', filenames=filenames, context=context)

    return AttachmentGroup(worker_type='screenshot', filenames=filenames, context=context)


def _classify_single_image(filename: str, dimensions: dict, context: str,
                           page_ctx: PageContext) -> str:
    """Classify a single image: histology_slide, diagram, or screenshot."""
    context_text = f"{context} {page_ctx.page_title} {page_ctx.page_text}".lower()

    # Check for histology/pathology context
    for pattern in CONTEXT_KEYWORDS['histology_slide']:
        if _keyword_match(pattern, context_text):
            return 'histology_slide'

    for pattern in CONTEXT_KEYWORDS['diagram']:
        if _keyword_match(pattern, context_text):
            return 'diagram'

    w, h = dimensions.get(filename, (1, 1))
    aspect = w / max(h, 1)

    # Wide landscape single image with structured content hints
    if aspect > 1.5 and any(
        re.search(rf'\b{kw}\b', context_text)
        for kw in ['org', 'team', 'structure', 'pipeline']
    ):
        return 'diagram'

    return 'screenshot'


def _order_by_embed_sequence(image_filenames: list[str], ctx: PageContext) -> list[str]:
    """Order images by their position in the page's embed sequence."""
    if not ctx.embed_order:
        return image_filenames

    image_set = set(image_filenames)
    ordered = [entry['filename'] for entry in ctx.embed_order
               if entry.get('type') in ('image', 'file') and entry.get('filename') in image_set]

    # Add any missing images at the end
    remaining = [f for f in image_filenames if f not in set(ordered)]
    return ordered + remaining


def _get_preceding_text(filename: str, ctx: PageContext) -> str:
    """Get text that appears immediately before a file in the embed order."""
    if not ctx.embed_order:
        return ""
    last_text = ""
    for entry in ctx.embed_order:
        if entry.get('filename') == filename:
            break
        if entry.get('type') == 'text' and entry.get('text'):
            last_text = entry['text']
    return last_text


def _get_text_between(file_a: str, file_b: str, ctx: PageContext) -> str:
    """Get text between two files in the embed order."""
    if not ctx.embed_order:
        return ""
    found_a = False
    texts = []
    for entry in ctx.embed_order:
        if entry.get('filename') == file_a:
            found_a = True
            continue
        if entry.get('filename') == file_b:
            break
        if found_a and entry.get('type') == 'text' and entry.get('text'):
            texts.append(entry['text'])
    return '\n'.join(texts)


def _is_speaker_separator(text: str) -> bool:
    """Heuristic: is this text a speaker/section separator between image groups?

    Only split on strong separators (headings, horizontal rules) to avoid
    over-fragmenting image groups on short annotation labels.
    """
    text = text.strip()
    if not text:
        return False
    # Markdown heading — always a section break
    if text.startswith('#'):
        return True
    # Horizontal rule
    if re.match(r'^-{3,}$|^\*{3,}$|^_{3,}$', text):
        return True
    # Multi-line text block (paragraph between images) — likely a new section
    if '\n' in text and len(text) > 100:
        return True
    return False
