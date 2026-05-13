"""Base class for all Vision AI workers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from vision_ai.detector import AttachmentGroup, PageContext


@dataclass
class AnalysisResult:
    title: str = ""
    content_type: str = ""
    authors: list[str] = field(default_factory=list)
    date: str = ""
    key_points: list[str] = field(default_factory=list)
    body: str = ""
    extra: dict = field(default_factory=dict)


def parse_structured_response(text: str, default_content_type: str = "") -> AnalysisResult:
    """Parse an LLM structured response into an AnalysisResult.

    Handles the standard format with TITLE:, CONTENT_TYPE:, AUTHORS:, DATE:,
    KEY_POINTS:, METHODS:, and BODY: sections.
    """
    result = AnalysisResult(content_type=default_content_type)

    lines = text.strip().split('\n')
    key_points = []
    body_lines = []
    in_body = False
    in_key_points = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('TITLE:'):
            result.title = stripped[6:].strip()
            in_body = False
            in_key_points = False
        elif stripped.startswith('CONTENT_TYPE:'):
            result.content_type = stripped[13:].strip()
            in_body = False
            in_key_points = False
        elif stripped.startswith('AUTHORS:'):
            authors_str = stripped[8:].strip()
            if authors_str.lower() != 'unknown':
                result.authors = [a.strip() for a in authors_str.split(',') if a.strip()]
            in_body = False
            in_key_points = False
        elif stripped.startswith('DATE:'):
            date_str = stripped[5:].strip()
            if date_str.lower() != 'unknown':
                result.date = date_str
            in_body = False
            in_key_points = False
        elif stripped.startswith('METHODS:'):
            methods = stripped[8:].strip()
            if methods:
                result.extra['methods'] = methods
            in_body = False
            in_key_points = False
        elif stripped == 'KEY_POINTS:':
            in_key_points = True
            in_body = False
        elif stripped == 'BODY:':
            in_body = True
            in_key_points = False
        elif in_key_points and stripped.startswith('- '):
            key_points.append(stripped[2:])
        elif in_body:
            body_lines.append(line)

    result.key_points = key_points
    result.body = '\n'.join(body_lines).strip()
    if not result.content_type:
        result.content_type = default_content_type
    return result


class VisionWorker(ABC):
    """Abstract base for content analysis workers."""

    @abstractmethod
    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> Optional[AnalysisResult]:
        """Analyze a group of attachments and return a structured result.

        Returns:
            AnalysisResult with structured fields + optional prose body.
            Return None to skip writing an AI note.
        """
        ...
