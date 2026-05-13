"""Base class for all Vision AI workers."""

from abc import ABC, abstractmethod

from vision_ai.detector import AttachmentGroup, PageContext


class VisionWorker(ABC):
    """Abstract base for content analysis workers."""

    @abstractmethod
    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> str:
        """Analyze a group of attachments and return markdown content (without frontmatter).

        Returns:
            Markdown string with the analysis. The router wraps this in frontmatter.
            Return empty string to skip writing an AI note.
        """
        ...
