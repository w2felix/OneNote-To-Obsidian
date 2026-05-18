"""Tier 2 entity extraction: LLM-based, piggybacked on the tagger API call.

This module provides the combined prompt and parsing logic for extracting
both tags and entities in a single Claude API call.
"""

import json
import logging
import re

from vision_ai.client import api_call_with_retry, VISION_MODEL

logger = logging.getLogger(__name__)

TAGGER_MODEL = VISION_MODEL

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
    "compounds": ["M1774"],
    "companies": ["Roche", "AstraZeneca"],
    "roles": ["Principal Scientist", "Director"],
    "methods": ["scRNA-seq", "CRISPR", "Deep Learning"]
  }
}

Tag rules:
- Lowercase, hyphenated (e.g., "machine-learning", "project-alpha")
- Specific enough to be useful for filtering and discovery
- NEVER use single common English words as tags (e.g., "research", "summary", "overview", "other", "general", "questions", "data", "analysis", "results", "discussion", "methods")
- DO include specific platform/tool names when central to the page (e.g., "gitlab", "docker", "nextflow", "rstudio", "jupyter")
- Cover: topic/domain, project/team if applicable, tools/platforms used, document-type if distinctive

Entity rules:
- genes: Use official HGNC gene symbols (uppercase). Include protein names as gene symbols.
- drugs: Use generic drug names (not brand names). Include investigational compounds by their research name.
- diseases: Use common abbreviations where standard (NSCLC, CRC, AML), otherwise use the full name.
- compounds: Internal codes matching M followed by 4 digits (e.g., M1774, M3814).
- companies: Pharma/biotech/diagnostics companies mentioned. Use canonical company names.
- roles: Organizational titles and positions (e.g., "Principal Scientist", "Associate Director", "Intern", "Group Leader"). Use the title as written.
- methods: Experimental methods, technologies, and computational approaches (e.g., "scRNA-seq", "CRISPR", "ChIP-seq", "Deep Learning", "Flow Cytometry"). Use canonical names.
- Only include entities actually mentioned in the text.
- If no biomedical entities are present, return empty lists."""


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

    response = api_call_with_retry(
        messages=[{"role": "user", "content": user_message}],
        system=COMBINED_SYSTEM_PROMPT,
        max_tokens=512,
        model=TAGGER_MODEL,
    )

    # Parse JSON response
    cleaned = response.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```\w*\n?', '', cleaned)
        cleaned = re.sub(r'\n?```$', '', cleaned)
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning(f"Combined tagger JSON parse error: {e}")
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
                'methods', 'clinical_trials', 'cell_lines', 'conferences', 'pathways'):
        vals = entities.get(key, [])
        if isinstance(vals, list):
            normalized_entities[key] = [str(v).strip() for v in vals if v]
        else:
            normalized_entities[key] = []

    return tags, normalized_entities
