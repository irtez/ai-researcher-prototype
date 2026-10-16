import tempfile
import uuid
from pathlib import Path
from typing import List, Tuple

import pymupdf
import pypdf
from PIL import Image

from academia_mcp.pdf import download_pdf


def parse_pdf_text(pdf_path: Path) -> List[str]:
    reader = pypdf.PdfReader(str(pdf_path))
    pages = []
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        if text:
            pages.append(f"## Page {page_num}\n\n{text}")
    return pages


def parse_pdf_images(pdf_path: Path) -> List[Image.Image]:
    doc = pymupdf.open(str(pdf_path))
    images = []
    for page in doc:
        pil_image: Image.Image = page.get_pixmap().pil_image()
        images.append(pil_image)
    return images


def get_pdf_filename(url: str) -> str:
    if "arxiv.org/pdf" in url:
        filename = url.split("/")[-1]
    else:
        filename = str(uuid.uuid4())
    if not filename.endswith(".pdf"):
        filename += ".pdf"
    return filename


def prepare_pdf_content(pdf_path: Path) -> Tuple[str, List[Image.Image]]:
    text_pages = parse_pdf_text(pdf_path)
    text = "\n\n\n".join(text_pages)
    images = parse_pdf_images(pdf_path)
    return text, images


def download_pdf_to_temp(url: str) -> Path:
    filename = get_pdf_filename(url)
    temp_path = Path(tempfile.gettempdir()) / filename
    if not temp_path.exists():
        download_pdf(url, temp_path)
    return temp_path
