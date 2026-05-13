"""OCR utilities using pytesseract."""

import os
import logging
from io import BytesIO

logger = logging.getLogger(__name__)

_tesseract_available = None


def _check_tesseract():
    global _tesseract_available
    if _tesseract_available is not None:
        return _tesseract_available
    try:
        import pytesseract
        conda_prefix = os.environ.get('CONDA_PREFIX')
        if conda_prefix and not os.environ.get('TESSDATA_PREFIX'):
            tessdata_dir = os.path.join(conda_prefix, 'Library', 'share', 'tessdata')
            if os.path.exists(tessdata_dir):
                os.environ['TESSDATA_PREFIX'] = tessdata_dir
        pytesseract.get_tesseract_version()
        _tesseract_available = True
    except Exception:
        _tesseract_available = False
        logger.debug("Tesseract not available, OCR pre-pass will be skipped")
    return _tesseract_available


def ocr_image(image) -> str:
    """Run OCR on a PIL Image. Returns extracted text or empty string."""
    if not _check_tesseract():
        return ""
    try:
        import pytesseract
        return pytesseract.image_to_string(image).strip()
    except Exception as e:
        logger.debug(f"OCR failed: {e}")
        return ""


def ocr_image_bytes(image_bytes: bytes) -> str:
    """Run OCR on raw image bytes."""
    if not _check_tesseract():
        return ""
    from PIL import Image
    img = Image.open(BytesIO(image_bytes))
    return ocr_image(img)


def ocr_pdf_page(pdf_bytes: bytes, page_num: int = 0, dpi: int = 200) -> str:
    """Render a single PDF page and OCR it."""
    if not _check_tesseract():
        return ""
    from vision_ai.vision_utils import pdf_to_images
    images = pdf_to_images(pdf_bytes, dpi=dpi)
    if page_num < len(images):
        return ocr_image(images[page_num])
    return ""
