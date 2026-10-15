"""Builds the app's assets from the documents the customs team shared.

    python scripts/prepare_assets.py [path/to/Docs]

Stamps and bonds carry real signatures, so they go to assets/private/, which
git ignores. So do the ODeX screenshots from the team's step-by-step guides: they
show a real ODeX account. Letterhead artwork goes to assets/letterheads/.
"""
from __future__ import annotations

import io
import shutil
import sys
import zipfile
from pathlib import Path

from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parents[1]
ASSETS = BACKEND_DIR / "assets"
PRIVATE = ASSETS / "private"

STAMPS = {
    "hexalog.png": "STAMP/HEXALOG GLOBAL.jpeg",
    "jai_shri.png": "STAMP/JAI SHRI.png",
    "envogue.png": "STAMP/Envoge sign.jpeg",
}

# (Word file, image inside it, saved as). Letterheads have no transparency, so
# JPEG keeps every generated letter small.
LETTERHEADS = [
    ("Docs/FDFSD.docx", "word/media/image2.png", "jsr_header.jpg"),
    ("Docs/FDFSD.docx", "word/media/image6.png", "jsr_footer.jpg"),
    ("Docs/CHECKLIST.docx", "word/media/image6.png", "cma_checklist_banner.jpg"),
    ("Docs/CHECKLIST.docx", "word/media/image23.png", "cma_office_use.jpg"),
]
LETTERHEAD_WIDTH = 1400

# The screens of the team's ODeX guides that the local ODeX shows, by the image's
# place in each Word file. local_portals/odex_screens.py places its buttons and
# fields over them, so a new guide needs its coordinates measured again.
ODEX_GUIDES = {
    # The invoice: request it, then collect it.
    "Docs/Delivery Order Request-First step 1.docx": {
        "word/media/image1.png": "service.png",  # step 1: the services, each with its location
        "word/media/image6.png": "dashboard.png",  # step 6: ePayment's main page and EDO DETAILS
        "word/media/image4.png": "liner-requests.png",  # step 4: Requests → Liner Requests
        "word/media/image5.png": "new-invoice.png",  # step 5: Request New Invoice
        "word/media/image7.png": "bl-status.png",  # step 8: the BL's status after EDO DETAILS
        "word/media/image8.png": "view-invoice.png",  # step 9: the request, with its invoice
    },
    # The DO request, once the DO folder is ready.
    "Docs/DO final steps corrected.docx": {
        "word/media/image3.png": "do-requests.png",  # step 3: Documents → DO Request, with its advertisement
        "word/media/image5.png": "do-request.png",  # steps 4-5: the DO request, its folder added through Add Row
    },
}

# Paper in a phone photo of a stamp is never pure white. Anything this close to
# white becomes fully transparent.
PAPER = 60
# A stamp prints about 35 mm wide; 400 px is roughly 300 dpi.
MAX_SIDE = 400


def transparent_stamp(source: Path, target: Path) -> None:
    """Ink keeps its colour; paper becomes transparent, so text under the stamp stays readable."""
    image = Image.open(source).convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    raw = image.tobytes()
    pixels = bytearray()
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        alpha = min(255, round((255 - min(r, g, b) - PAPER) * 255 / (255 - PAPER) * 1.6))
        if alpha <= 0:
            pixels += b"\xff\xff\xff\x00"
            continue
        a = alpha / 255
        # Undo the white paper mixed into the colour, so the ink isn't washed out.
        pixels += bytes(max(0, min(255, round((c - 255 * (1 - a)) / a))) for c in (r, g, b))
        pixels.append(alpha)
    out = Image.frombytes("RGBA", image.size, bytes(pixels))
    target.parent.mkdir(parents=True, exist_ok=True)
    out.save(target)


def main() -> None:
    docs = Path(sys.argv[1]) if len(sys.argv) > 1 else BACKEND_DIR.parent / "Docs"
    if not docs.is_dir():
        sys.exit(f"No Docs folder at {docs}. Pass its path as the first argument.")

    for name, source in STAMPS.items():
        transparent_stamp(docs / source, PRIVATE / "stamps" / name)
        print(f"stamp      assets/private/stamps/{name}")

    shutil.copyfile(docs / "Docs/Bonds.pdf", PRIVATE / "bonds.pdf")
    print("bonds      assets/private/bonds.pdf")

    for docx, member, name in LETTERHEADS:
        target = ASSETS / "letterheads" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(docs / docx) as archive:
            image = Image.open(io.BytesIO(archive.read(member)))
        flat = Image.new("RGB", image.size, "white")
        flat.paste(image, mask=image.convert("RGBA").getchannel("A"))
        flat.thumbnail((LETTERHEAD_WIDTH, LETTERHEAD_WIDTH))
        flat.save(target, quality=88, optimize=True)
        print(f"letterhead assets/letterheads/{name}")

    for guide_name, screens in ODEX_GUIDES.items():
        guide = docs / guide_name
        if not guide.is_file():
            print(f"odex       skipped: no {guide_name} (its pages then show grey stand-ins)")
            continue
        with zipfile.ZipFile(guide) as archive:
            for member, name in screens.items():
                target = PRIVATE / "odex" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
                print(f"odex       assets/private/odex/{name}")


if __name__ == "__main__":
    main()
