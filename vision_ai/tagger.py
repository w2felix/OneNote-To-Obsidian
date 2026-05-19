"""AI semantic tagging for OneNote pages using Claude API."""

import hashlib
import json
import logging
import re
from pathlib import Path

from vision_ai.client import api_call_with_retry, TAGGER_MODEL

logger = logging.getLogger(__name__)
MIN_WORD_COUNT = 50


def _extract_json_value(text: str) -> str:
    """Extract the first complete JSON value (object or array) from text.

    Handles the case where the LLM appends commentary after the JSON.
    """
    if not text:
        return text
    open_ch = text[0]
    if open_ch == '{':
        close_ch = '}'
    elif open_ch == '[':
        close_ch = ']'
    else:
        return text
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[:i + 1]
    return text

# Tag normalization vocabulary (loaded lazily)
_tag_synonym_map: dict[str, str] | None = None


def _load_tag_vocabulary() -> dict[str, str]:
    """Load tag_vocabulary.yaml and build synonym → canonical mapping."""
    global _tag_synonym_map
    if _tag_synonym_map is not None:
        return _tag_synonym_map

    vocab_path = Path(__file__).resolve().parent.parent / 'entity_data' / 'tag_vocabulary.yaml'
    _tag_synonym_map = {}
    if not vocab_path.exists():
        return _tag_synonym_map

    try:
        import yaml
        with open(vocab_path, encoding='utf-8') as f:
            vocab = yaml.safe_load(f) or {}
        for canonical, synonyms in vocab.items():
            _tag_synonym_map[canonical] = canonical
            if isinstance(synonyms, list):
                for syn in synonyms:
                    _tag_synonym_map[syn] = canonical
    except ImportError:
        # Fallback: simple YAML parsing without pyyaml
        current_key = None
        for line in vocab_path.read_text(encoding='utf-8').splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if not line.startswith(' ') and stripped.endswith(':'):
                current_key = stripped.rstrip(':').strip()
                _tag_synonym_map[current_key] = current_key
            elif current_key and stripped.startswith('- '):
                syn = stripped.removeprefix('- ').strip()
                _tag_synonym_map[syn] = current_key

    return _tag_synonym_map


def _normalize_tag_vocabulary(tag: str) -> str:
    """Map a tag to its canonical form using the vocabulary."""
    vocab = _load_tag_vocabulary()
    return vocab.get(tag, tag)

SYSTEM_PROMPT = """You are a knowledge-base taxonomist. Given this note, return 3-7 topic tags.
Rules:
- Lowercase, hyphenated (e.g., "machine-learning", "project-alpha")
- ALWAYS in English, even if the note is in German or another language (e.g., "Chemiker" → "chemistry", "Sequenzierung" → "sequencing")
- Specific enough to be useful for filtering and discovery
- NEVER use single common English words as tags (e.g., "research", "summary", "overview", "other", "general", "questions", "data", "analysis", "results", "discussion", "methods")
- DO include specific platform/tool names when central to the page (e.g., "gitlab", "docker", "nextflow", "rstudio", "jupyter")
- Cover: topic/domain, project/team if applicable, tools/platforms used, document-type if distinctive
- Return ONLY a JSON array of strings, nothing else."""


def _strip_frontmatter(markdown: str) -> str:
    """Remove YAML frontmatter from markdown, return body only."""
    if not markdown.startswith('---'):
        return markdown
    try:
        end_idx = markdown.index('---', 3) + 3
        return markdown[end_idx:]
    except ValueError:
        return markdown


def _content_hash(text: str) -> str:
    """Compute SHA-256 hash of content for cache comparison."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def generate_tags(markdown_body: str, page_title: str, section_path: str,
                  existing_tags: list[str], state: dict, page_key: str,
                  force: bool = False) -> list[str]:
    """Call Claude to generate 3-7 semantic tags for a page.

    Args:
        markdown_body: Full markdown content (with frontmatter)
        page_title: Title of the page
        section_path: Notebook/section path
        existing_tags: Tags already present from OneNote
        state: Sync state dict (modified in place for caching)
        page_key: Unique key for this page in state
        force: If True, re-tag even if content unchanged

    Returns:
        List of tag strings (lowercase-hyphenated), or empty list if skipped.
    """
    body = _strip_frontmatter(markdown_body)

    # Skip trivial/empty pages
    word_count = len(body.split())
    if word_count < MIN_WORD_COUNT:
        return []

    # Compute content hash for caching
    body_hash = _content_hash(body)

    # Check cache
    if 'ai_tags' not in state:
        state['ai_tags'] = {}

    cached = state['ai_tags'].get(page_key)
    if cached and not force and cached.get('hash') == body_hash:
        logger.debug(f"AI tags cache hit for {page_title}")
        return cached['tags']

    # Truncate body for the prompt (save tokens)
    body_truncated = body[:3000]

    user_message = f"""Page title: {page_title}
Section: {section_path}
Content:
{body_truncated}"""

    try:
        response = api_call_with_retry(
            messages=[{"role": "user", "content": user_message}],
            system=SYSTEM_PROMPT,
            max_tokens=256,
            model=TAGGER_MODEL,
        )

        # Parse JSON array from response
        # Strip any markdown code fences if present
        cleaned = response.strip()
        if cleaned.startswith('```'):
            cleaned = re.sub(r'^```\w*\n?', '', cleaned)
            cleaned = re.sub(r'\n?```\s*$', '', cleaned)
            cleaned = cleaned.strip()

        # Strip trailing text after the JSON array closes
        cleaned = _extract_json_value(cleaned)

        tags = json.loads(cleaned)

        if not isinstance(tags, list):
            logger.warning(f"AI tags returned non-list for {page_title}: {type(tags)}")
            return []

        # Normalize tags
        normalized = []
        for tag in tags:
            if isinstance(tag, str):
                slug = tag.lower().strip().replace(' ', '-')
                slug = re.sub(r'[^a-z0-9_/\-]', '', slug)
                slug = slug[:80]  # limit tag length for Obsidian compatibility
                if slug:
                    # Map to canonical form via vocabulary
                    slug = _normalize_tag_vocabulary(slug)
                    if slug not in normalized:
                        normalized.append(slug)

        # Deduplicate against existing tags
        existing_set = set(existing_tags)
        final_tags = [t for t in normalized if t not in existing_set]

        # Update cache
        state['ai_tags'][page_key] = {
            'hash': body_hash,
            'tags': final_tags,
        }

        logger.debug(f"AI tags generated for {page_title}: {final_tags}")
        return final_tags

    except json.JSONDecodeError as e:
        logger.warning(f"AI tags JSON parse error for {page_title}: {e}")
        return []
    except Exception as e:
        logger.warning(f"AI tags API error for {page_title}: {e}")
        raise
