"""Reading uploaded files, stamping their pages, and joining the folder into one PDF."""
from __future__ import annotations

import io
import re
import textwrap
from collections.abc import Iterable
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .folder import Placement

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
_NOT_ALNUM = re.compile(r"[^A-Z0-9]")


class UnreadableFile(ValueError):
    """The upload is not a PDF or image we can open."""


def compact(text: str) -> str:
    """Upper case letters and digits only, so 'MSEC-260803047 -251' matches 'MSEC260803047251'."""
    return _NOT_ALNUM.sub("", text.upper())


def to_pdf(data: bytes, filename: str) -> bytes:
    """The upload as a PDF. Scans sent as photos become one page per image."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".rtf":
        return _rtf_to_pdf(data, filename)
    if suffix in IMAGE_TYPES:
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (UnidentifiedImageError, OSError) as exc:
            raise UnreadableFile(f"{filename} could not be opened as an image.") from exc
        out = io.BytesIO()
        image.convert("RGB").save(out, "PDF", resolution=150)
        return out.getvalue()
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UnreadableFile(f"{filename} is password protected. Ask the sender for an unlocked copy.")
        if not reader.pages:
            raise UnreadableFile(f"{filename} has no pages.")
    except (PdfReadError, ValueError, OSError) as exc:
        if isinstance(exc, UnreadableFile):
            raise
        raise UnreadableFile(f"{filename} is not a PDF or an image (JPG or PNG).") from exc
    return data


def _rtf_to_pdf(data: bytes, filename: str) -> bytes:
    """A Word/RTF file as plain text on pages, with a text layer the checks can read.
    shortcut: layout and fonts are lost; send a PDF when the look matters."""
    from striprtf.striprtf import rtf_to_text

    try:
        text = rtf_to_text(data.decode("latin-1"))
    except Exception as exc:  # noqa: BLE001 - striprtf raises whatever it trips on
        raise UnreadableFile(f"{filename} could not be read as a Word/RTF file.") from exc
    lines = [part for line in text.splitlines() for part in (textwrap.wrap(line, 95) or [""])]
    if not any(line.strip() for line in lines):
        raise UnreadableFile(f"{filename} has no text.")
    out = io.BytesIO()
    sheet = canvas.Canvas(out, pagesize=(595, 842))
    for i, line in enumerate(lines):
        row = i % 70
        if i and row == 0:
            sheet.showPage()
        if row == 0:
            sheet.setFont("Courier", 9)
        sheet.drawString(40, 800 - row * 11.2, line)
    sheet.showPage()
    sheet.save()
    return out.getvalue()


def page_count(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def extract_text(pdf: bytes) -> str:
    """The text layer of every page. Scans without one give an empty string."""
    try:
        reader = PdfReader(io.BytesIO(pdf))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:  # noqa: BLE001 - a broken text layer just means nothing to match
        return ""


def _stamp_overlay(width: float, height: float, placement: Placement, stamp: Path):
    """A transparent page the size of the target with only the stamp on it."""
    image = ImageReader(str(stamp))
    image_w, image_h = image.getSize()
    stamp_w = placement.width * width
    stamp_h = stamp_w * image_h / image_w
    buffer = io.BytesIO()
    sheet = canvas.Canvas(buffer, pagesize=(width, height))
    sheet.drawImage(
        image,
        placement.x * width - stamp_w / 2,
        placement.y * height - stamp_h / 2,
        stamp_w,
        stamp_h,
        mask="auto",
    )
    sheet.save()
    return PdfReader(io.BytesIO(buffer.getvalue())).pages[0]


def add_pages(
    writer: PdfWriter,
    pdf: bytes | Path,
    pages: Iterable[int] = (),
    placement: Placement | None = None,
    stamp: Path | None = None,
) -> None:
    """Appends pages (1-based; all when empty), stamping them when a placement is given."""
    reader = PdfReader(pdf if isinstance(pdf, Path) else io.BytesIO(pdf))
    numbers = list(pages) or list(range(1, len(reader.pages) + 1))
    for position, number in enumerate(numbers):
        if not 1 <= number <= len(reader.pages):
            raise UnreadableFile(f"Page {number} does not exist; the file has {len(reader.pages)}.")
        # Stamp the writer's copy: pypdf only edits pages reliably once they belong to a writer.
        page = writer.add_page(reader.pages[number - 1])
        if placement and stamp and (placement.pages == "all" or position == 0):
            if page.rotation:
                page.transfer_rotation_to_content()
            box = page.cropbox
            overlay = _stamp_overlay(float(box.width), float(box.height), placement, stamp)
            # The overlay starts at 0,0; move it to where the visible box starts.
            page.merge_translated_page(overlay, float(box.left), float(box.bottom))


def write(writer: PdfWriter) -> bytes:
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
