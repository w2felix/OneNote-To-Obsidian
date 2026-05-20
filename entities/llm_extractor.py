"""Tier 2 entity extraction: LLM-based, piggybacked on the tagger API call.

This module provides the combined prompt and parsing logic for extracting
both tags and entities in a single Claude API call.
"""

import json
import logging
import re

from vision_ai.client import api_call_with_retry, TAGGER_MODEL

logger = logging.getLogger(__name__)

COMBINED_SYSTEM_PROMPT = """You are a knowledge-base taxonomist and biomedical annotator. Given this note, return:
1. 3-7 topic tags (lowercase, hyphenated)
2. Biomedical entities and organizational roles found in the text

Return ONLY a JSON object with this structure:
{
  "tags": ["tag-1", "tag-2"],
  "entities": {
    "genes": ["EGFR", "KRAS"],
    "drugs": ["pembrolizumab", "osimertinib"],
    "diseases": ["NSCLC", "melanoma"],
    "compounds": ["M1774", "MSC0012345"],
    "companies": ["Roche", "AstraZeneca"],
    "roles": ["Principal Scientist", "Director"],
    "methods": ["scRNA-seq", "CRISPR", "Deep Learning"]
  }
}

Tag rules:
- Lowercase, hyphenated (e.g., "machine-learning", "project-alpha")
- ALWAYS in English, even if the note is in German or another language (e.g., "Chemiker" → "chemistry", "Sequenzierung" → "sequencing")
- Specific enough to be useful for filtering and discovery
- NEVER use single common English words as tags (e.g., "research", "summary", "overview", "other", "general", "questions", "data", "analysis", "results", "discussion", "methods")
- DO include specific platform/tool names when central to the page (e.g., "gitlab", "docker", "nextflow", "rstudio", "jupyter")
- Cover: topic/domain, project/team if applicable, tools/platforms used, document-type if distinctive

Entity rules:
- genes: Use official HGNC gene symbols (uppercase). Include protein names as gene symbols.
- drugs: Use generic drug names (not brand names). Include investigational compounds by their research name.
- diseases: Use common abbreviations where standard (NSCLC, CRC, AML), otherwise use the full name.
- compounds: Internal codes matching M followed by 4 digits (e.g., M1774, M3814) or MSC followed by 6–9 digits (chemical library numbers, e.g., MSC0012345).
- companies: Pharma/biotech/diagnostics companies mentioned. Use canonical company names.
- roles: Corporate/organizational job titles only (e.g., "Principal Scientist", "Associate Director", "Intern", "Group Leader", "Head of Data Science"). Do NOT include academic degrees (PhD, MSc), disciplines (Bioinformatics, Biology), or team names as roles.
- methods: Experimental methods, technologies, and computational approaches (e.g., "scRNA-seq", "CRISPR", "ChIP-seq", "Deep Learning", "Flow Cytometry"). Use canonical names.
- Only include entities actually mentioned in the text.
- If no biomedical entities are present, return empty lists."""


MAX_TOKENS = 1024
RETRY_MAX_TOKENS = 2048


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


def _clean_json_response(raw: str) -> str:
    """Extract JSON from an LLM response, handling code fences and preamble."""
    cleaned = raw.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```\w*\n?', '', cleaned)
        cleaned = re.sub(r'\n?```\s*$', '', cleaned)
        cleaned = cleaned.strip()
    # Handle preamble text before JSON
    if not cleaned.startswith(('{', '[')):
        match = re.search(r'[{\[]', cleaned)
        if match:
            cleaned = cleaned[match.start():]
    # Strip trailing text after the JSON value closes
    cleaned = _extract_json_value(cleaned)
    return cleaned


def extract_tags_and_entities(body_text: str, page_title: str,
                              section_path: str) -> tuple[list[str], dict]:
    """Call Claude to extract both tags and entities in a single API call.

    Returns:
        (tags_list, entities_dict) where entities_dict has keys:
        genes, drugs, diseases, compounds — each a list of strings.
    """
    body_truncated = body_text[:3000]

    user_message = f"""Page title: {page_title}
Section: {section_path}
Content:
{body_truncated}"""

    messages = [{"role": "user", "content": user_message}]

    response, stop_reason = api_call_with_retry(
        messages=messages,
        system=COMBINED_SYSTEM_PROMPT,
        max_tokens=MAX_TOKENS,
        model=TAGGER_MODEL,
        return_stop_reason=True,
    )

    # Retry once with higher budget if truncated
    if stop_reason == "max_tokens":
        logger.debug("Combined tagger hit max_tokens, retrying with higher budget")
        response, stop_reason = api_call_with_retry(
            messages=messages,
            system=COMBINED_SYSTEM_PROMPT,
            max_tokens=RETRY_MAX_TOKENS,
            model=TAGGER_MODEL,
            return_stop_reason=True,
        )

    cleaned = _clean_json_response(response)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning(f"Combined tagger JSON parse error: {e}")
        logger.debug(f"Raw response ({len(response)} chars, stop={stop_reason}): {response[:500]}")
        return [], {}

    if not isinstance(data, dict):
        logger.warning(f"Combined tagger returned non-dict: {type(data)}")
        return [], {}

    tags = data.get('tags', [])
    if not isinstance(tags, list):
        tags = []

    entities = data.get('entities', {})
    if not isinstance(entities, dict):
        entities = {}

    # Normalize entity lists
    normalized_entities = {}
    for key in ('genes', 'drugs', 'diseases', 'compounds', 'companies', 'roles',
                'methods', 'clinical_trials', 'cell_lines', 'conferences', 'pathways',
                'departments'):
        vals = entities.get(key, [])
        if isinstance(vals, list):
            normalized_entities[key] = [str(v).strip() for v in vals if v]
        else:
            normalized_entities[key] = []

    return tags, normalized_entities
