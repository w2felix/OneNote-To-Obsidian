"""Reproduce the 'Combined tagger JSON parse error' for diagnosis.

Loads the failing page, calls the LLM with the same parameters as the pipeline,
and prints the raw response to reveal truncation or malformed JSON.
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vision_ai.client import get_client, TAGGER_MODEL
from entities.llm_extractor import COMBINED_SYSTEM_PROMPT, MAX_TOKENS, _clean_json_response

PAGE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "obsidian_export",
    "Computational Oncology Notebook",
    "CC ADC Topics",
    "Avb6_ITGB6 prevalence.md",
)


def strip_frontmatter(markdown: str) -> str:
    if not markdown.startswith('---'):
        return markdown
    try:
        end_idx = markdown.index('---', 3) + 3
        return markdown[end_idx:]
    except ValueError:
        return markdown


def test_with_max_tokens(body: str, page_title: str, max_tokens: int):
    body_truncated = body[:3000]
    user_message = f"""Page title: {page_title}
Section: CC ADC Topics
Content:
{body_truncated}"""

    print(f"\n{'='*60}")
    print(f"Testing with max_tokens={max_tokens}")
    print(f"{'='*60}")

    client = get_client()
    response = client.messages.create(
        model=TAGGER_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": user_message}],
        system=COMBINED_SYSTEM_PROMPT,
    )

    raw_text = response.content[0].text
    stop_reason = response.stop_reason
    usage = response.usage

    print(f"Stop reason: {stop_reason}")
    print(f"Output tokens: {usage.output_tokens}")
    print(f"Response length: {len(raw_text)} chars")
    print(f"\n--- RAW RESPONSE ---")
    print(raw_text)
    print(f"--- END RAW RESPONSE ---\n")

    # Try parsing
    cleaned = _clean_json_response(raw_text)

    try:
        data = json.loads(cleaned)
        print(f"JSON parse: SUCCESS")
        print(f"Tags: {data.get('tags', [])}")
        entities = data.get('entities', {})
        for k, v in entities.items():
            if v:
                print(f"  {k}: {v}")
    except json.JSONDecodeError as e:
        print(f"JSON parse: FAILED — {e}")
        # Show around the error position
        pos = e.pos
        if pos is not None:
            start = max(0, pos - 50)
            end = min(len(cleaned), pos + 50)
            print(f"Context around error (pos {pos}):")
            print(f"  ...{cleaned[start:pos]}<<<HERE>>>{cleaned[pos:end]}...")


def main():
    if not os.path.exists(PAGE_PATH):
        print(f"Page not found: {PAGE_PATH}")
        sys.exit(1)

    with open(PAGE_PATH, 'r', encoding='utf-8') as f:
        markdown = f.read()

    body = strip_frontmatter(markdown)
    page_title = "Avb6/ITGB6 prevalence"

    print(f"Page body length: {len(body)} chars")
    print(f"Body[:3000] word count: {len(body[:3000].split())}")

    # Test with production max_tokens
    test_with_max_tokens(body, page_title, max_tokens=MAX_TOKENS)

    # Test with lower budget to reproduce truncation issues
    test_with_max_tokens(body, page_title, max_tokens=512)


if __name__ == "__main__":
    main()
