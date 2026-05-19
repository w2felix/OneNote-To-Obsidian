"""Route detected content types to the appropriate worker and orchestrate analysis."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from vision_ai.detector import PageContext, AttachmentGroup, detect_content_types
from vision_ai.cache import (
    load_cache, save_cache, content_hash, group_hash, is_cached, update_cache
)
from vision_ai.output import (
    AI_NOTES_FOLDER, generate_ai_note_filename, write_ai_note, inject_callout_links
)

logger = logging.getLogger(__name__)

MAX_CONCURRENT_WORKERS = 5


WORKER_REGISTRY = {
    'tabular_data': 'vision_ai.workers.tabular_data.TabularDataWorker',
    'screenshot': 'vision_ai.workers.screenshot.ScreenshotWorker',
    'diagram': 'vision_ai.workers.diagram.DiagramWorker',
    'slide_photo': 'vision_ai.workers.slide_photo.SlidePhotoWorker',
    'poster': 'vision_ai.workers.poster.PosterWorker',
    'document': 'vision_ai.workers.document.DocumentWorker',
    'presentation': 'vision_ai.workers.presentation.PresentationWorker',
    'histology_slide': 'vision_ai.workers.histology_slide.HistologySlideWorker',
}


def _get_worker(worker_type: str):
    """Lazy-load the appropriate worker."""
    path = WORKER_REGISTRY.get(worker_type)
    if not path:
        logger.warning(f"Unknown worker type: {worker_type}")
        return None
    module_path, class_name = path.rsplit('.', 1)
    from importlib import import_module
    module = import_module(module_path)
    return getattr(module, class_name)()


def analyze_page_attachments(page_md_path: Path, attachments_dir: Path,
                             images: dict[str, bytes], page_context: dict,
                             force: bool = False):
    """Main entry point: detect, route, analyze, write AI notes, inject links.

    Args:
        page_md_path: Path to the parent markdown file
        attachments_dir: Path to the _attachments/ directory
        images: dict of filename -> raw bytes (from convert_page_xml)
        page_context: dict with page_name, section_path, markdown_text, embed_order
        force: if True, re-analyze even if cached
    """
    if not images:
        return

    ctx = PageContext(
        page_title=page_context.get('page_name', ''),
        page_text=page_context.get('markdown_text', ''),
        section_path=page_context.get('section_path', ''),
        parent_page=page_context.get('parent_page'),
        embed_order=page_context.get('embed_order', []),
    )

    # Detect and group
    groups = detect_content_types(images, ctx)
    if not groups:
        return

    logger.info(f"Vision AI: {page_md_path.stem} — {len(groups)} group(s) detected")

    # Setup AI notes directory
    ai_notes_dir = page_md_path.parent / AI_NOTES_FOLDER
    cache = load_cache(ai_notes_dir)

    callouts_to_inject = []
    if len(groups) >= 2:
        with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_WORKERS, len(groups))) as pool:
            futures = {
                pool.submit(_process_group, group, images, ctx, ai_notes_dir, cache, force): group
                for group in groups
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        callouts_to_inject.append(result)
                except Exception as e:
                    group = futures[future]
                    logger.warning(f"  Vision AI worker failed: {group.worker_type}: {e}")
    else:
        for group in groups:
            result = _process_group(group, images, ctx, ai_notes_dir, cache, force)
            if result:
                callouts_to_inject.append(result)

    # Inject all callout links in a single file read/write pass
    if callouts_to_inject:
        inject_callout_links(page_md_path, callouts_to_inject)

    save_cache(ai_notes_dir, cache)


def _process_group(group: AttachmentGroup, images: dict[str, bytes],
                   ctx: PageContext, ai_notes_dir: Path,
                   cache: dict, force: bool):
    """Process a single attachment group. Returns (group, ai_note_filename) or None."""
    # Compute cache key and hash
    if len(group.filenames) == 1:
        cache_key = Path(group.filenames[0]).stem
        fname = group.filenames[0]
        if fname not in images:
            logger.warning(f"  Skipping {fname}: not found in images dict")
            return None
        current_hash = content_hash(images[fname])
    else:
        # Filter out any filenames missing from the images dict
        group.filenames = [f for f in group.filenames if f in images]
        if not group.filenames:
            return None
        cache_key = f"{group.worker_type}_{'_'.join(Path(f).stem[:6] for f in group.filenames[:3])}"
        current_hash = group_hash(group.filenames, images)

    # Check cache
    if not force and is_cached(cache, cache_key, current_hash, ai_notes_dir):
        logger.debug(f"  Cached: {cache_key}")
        return None

    # Clean up old file with bad characters in name
    old_entry = cache.get('entries', {}).get(cache_key)
    if old_entry:
        old_file = old_entry.get('ai_note_file', '')
        if any(c in old_file for c in '#[]') and (ai_notes_dir / old_file).exists():
            (ai_notes_dir / old_file).unlink()
            logger.info(f"  Removed bad-name file: {old_file}")

    # Get worker
    worker = _get_worker(group.worker_type)
    if worker is None:
        return None

    # Run analysis
    logger.info(f"  Analyzing: {group.worker_type} ({len(group.filenames)} file(s))")
    try:
        analysis_result = worker.analyze(group, images, ctx)
    except Exception as e:
        logger.error(f"  Worker {group.worker_type} failed: {e}")
        return None

    if not analysis_result:
        logger.warning(f"  Worker {group.worker_type} returned empty result")
        return None

    # Write AI note with structured YAML frontmatter
    ai_note_filename = generate_ai_note_filename(group)
    write_ai_note(ai_notes_dir, ai_note_filename, analysis_result, group)

    # Update cache
    update_cache(cache, cache_key, current_hash, ai_note_filename, group.worker_type)

    return (group, ai_note_filename)
