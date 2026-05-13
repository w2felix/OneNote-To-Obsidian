"""
Vision AI module for OneNote-to-Obsidian.

Optional module that analyzes embedded files (images, PDFs, XLSX) using Vision AI
and produces structured "index card" markdown summaries stored in _ai_notes/.

Usage:
    from vision_ai import analyze_page_attachments
    analyze_page_attachments(page_md_path, attachments_dir, images, page_context)

Enable via: python onenote_to_obsidian.py --vision-ai
"""

from vision_ai.router import analyze_page_attachments
from vision_ai.tagger import generate_tags

__all__ = ['analyze_page_attachments', 'generate_tags']
