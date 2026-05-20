"""Shared Vision AI utilities: image encoding, PDF-to-image."""

import base64
import logging
from io import BytesIO

logger = logging.getLogger(__name__)

MAX_IMAGE_DIMENSION = 2048
JPEG_QUALITY = 85


def _init_fitz():
    """Import fitz and suppress noisy MuPDF structure-tree warnings."""
    import fitz
    try:
        fitz.TOOLS.mupdf_display_errors(False)
    except (AttributeError, TypeError):
        pass
    return fitz


def pdf_to_images(pdf_bytes: bytes, dpi: int = 150, max_pages: int = 50,
                   page_indices: list[int] | None = None) -> list:
    """Render PDF pages to PIL Images at given DPI.

    Args:
        page_indices: If provided, render only these specific page indices.
        max_pages: Safety cap when page_indices is not specified.
    """
    fitz = _init_fitz()
    from PIL import Image

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    if page_indices is not None:
        for idx in page_indices:
            if 0 <= idx < len(doc):
                pix = doc[idx].get_pixmap(matrix=matrix)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                images.append(img)
    else:
        for page in doc[:max_pages]:
            pix = page.get_pixmap(matrix=matrix)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            images.append(img)

    doc.close()
    return images


def pdf_page_count(pdf_bytes: bytes) -> int:
    fitz = _init_fitz()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    count = len(doc)
    doc.close()
    return count


def pdf_metadata(pdf_bytes: bytes) -> dict:
    """Extract basic PDF metadata: page count, orientation, text density."""
    fitz = _init_fitz()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)

    total_chars = 0
    is_landscape = False
    if page_count > 0:
        first_page = doc[0]
        rect = first_page.rect
        is_landscape = rect.width > rect.height
        for page in doc:
            total_chars += len(page.get_text())

    doc.close()
    return {
        'page_count': page_count,
        'is_landscape': is_landscape,
        'total_chars': total_chars,
        'chars_per_page': total_chars / max(page_count, 1),
        'file_size': len(pdf_bytes),
    }


def encode_image(image, max_dim: int = MAX_IMAGE_DIMENSION,
                 quality: int = JPEG_QUALITY) -> str:
    """Resize PIL Image to max dimension and encode as base64 JPEG."""
    from PIL import Image

    w, h = image.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    if image.mode != 'RGB':
        image = image.convert('RGB')

    buf = BytesIO()
    image.save(buf, format='JPEG', quality=quality)
    return base64.b64encode(buf.getvalue()).decode('utf-8')


def encode_image_bytes(image_bytes: bytes, max_dim: int = MAX_IMAGE_DIMENSION) -> str:
    """Encode raw image bytes (PNG/JPG) to base64 JPEG, resized."""
    from PIL import Image
    image = Image.open(BytesIO(image_bytes))
    return encode_image(image, max_dim)


def image_dimensions(image_bytes: bytes) -> tuple[int, int]:
    """Get (width, height) of an image from raw bytes."""
    from PIL import Image
    img = Image.open(BytesIO(image_bytes))
    return img.size


def build_vision_message(images_b64: list[str], prompt: str) -> list[dict]:
    """Build a messages list with multiple images + text prompt."""
    content = []
    for img_b64 in images_b64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}
        })
    content.append({"type": "text", "text": prompt})
    return [{"role": "user", "content": content}]
