"""Reading and checking what the portals' forms send, the way a portal would."""
from __future__ import annotations

import re
from datetime import date, datetime

from starlette.datastructures import FormData, UploadFile

MAX_UPLOAD = 25 * 1024 * 1024

BL = re.compile(r"^[A-Z0-9]{6,25}$")
CONTAINER = re.compile(r"^[A-Z]{4}\d{7}$")
UTR = re.compile(r"^[A-Z0-9]{6,30}$")
INVOICE_NO = re.compile(r"^[A-Z0-9-]{4,30}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Rejected(Exception):
    """The portal turns the form down. Raised inside Store.change(), it also drops
    whatever the block changed."""

    def __init__(self, *problems: str) -> None:
        super().__init__(" ".join(problems))
        self.problems = list(problems)


def text(form: FormData, key: str) -> str:
    value = form.get(key)
    return value.strip() if isinstance(value, str) else ""


def code(value: str) -> str:
    """A reference as portals store them: no spaces, capitals."""
    return re.sub(r"\s+", "", value).upper()


def containers(value: str) -> list[str]:
    return [c for c in (code(part) for part in re.split(r"[,;\n]+", value)) if c]


def amount(value: str) -> float | None:
    try:
        number = float(value.replace(",", ""))
    except ValueError:
        return None
    return round(number, 2) if number > 0 else None


def emails(value: str) -> list[str] | None:
    """One to three addresses separated by commas, as CMA CGM's pages take them."""
    found = [part.strip() for part in value.split(",") if part.strip()]
    if not 1 <= len(found) <= 3 or not all(EMAIL.match(address) for address in found):
        return None
    return found


def past_dmy_date(value: str) -> str | None:
    """A DD-MM-YYYY date, today or earlier, as ISO."""
    try:
        day = datetime.strptime(value, "%d-%m-%Y").date()
    except ValueError:
        return None
    return day.isoformat() if day <= date.today() else None


def past_date(value: str) -> str | None:
    """An ISO date, today or earlier: a payment can't be dated in the future."""
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return None
    return value if day <= date.today() else None


async def attachment(
    form: FormData, key: str, what: str, problems: list[str], *, required: bool
) -> tuple[str, bytes] | None:
    """An attached PDF as (file name, content), or a problem saying what's wrong."""
    upload = form.get(key)
    if not isinstance(upload, UploadFile) or not upload.filename:
        if required:
            problems.append(f"Attach the {what}.")
        return None
    content = await upload.read()
    if len(content) > MAX_UPLOAD:
        problems.append(f"The {what} is larger than 25 MB.")
    elif not content.startswith(b"%PDF-"):
        problems.append(f"The {what} must be a PDF.")
    else:
        return upload.filename, content
    return None
