"""Reads CMA CGM's import tax invoice, the DO invoice the browser agent collects from
ODeX: its charges table, its GST table and its totals.

The PDF gives its text column by column, so each row is rebuilt from where its pieces
sit on the page. An invoice counts as read only when its numbers agree: the charges
make the total excluding tax, the GST lines make the total tax, and the two make the
total. When they don't, ``problems`` says where.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

ROW_HEIGHT = 4.5  # points: pieces of text closer than this in height are on one row
CENTS = 0.05  # rounding the invoice itself allows between a total and its lines

_MONEY = r"-?[\d,]+\.\d{2}"
_CHARGE = re.compile(
    rf"^(?P<size>\d{{2}}[A-Z0-9]{{2}})\s+[A-Z]\s+(?P<description>.+?)\s+(?P<tax>[A-Z]{{2}})\s+(?P<quantity>\d+)\s*"
    rf"(?P<basis>[A-Z]{{3}})\s+(?P<rate>{_MONEY})\s*(?P<currency>[A-Z]{{3}})\s+(?P<amount>{_MONEY})\s+(?P<inr>{_MONEY})$"
)
_TAX = re.compile(
    rf"^(?P<description>.+?)\s+(?P<sac>\d{{6}})\s+(?P<state>[A-Z][A-Z .&-]*?)\s+(?P<tax>[A-Z]{{2}})\s+"
    rf"(?P<taxable>{_MONEY})\s+(?P<amount>{_MONEY})$"
)
# The invoice number, not the carrier number printed in the same style ("Carrier No. INCMA…").
_INVOICE_NO = re.compile(r"(?<!Carrier No\. )\b(IN[A-Z]{2,6}\d{6,10}|INV-DEMO-\d+)\b")
_DATE = r"(\d{2}-[A-Z]{3}-\d{4})"
_CONTAINER = re.compile(r"\b[A-Z]{4}\d{7}\b")


class UnreadableInvoice(Exception):
    """The file isn't a PDF, or no CMA CGM invoice is in it."""


def read(pdf: bytes) -> dict[str, Any]:
    """The invoice's header, both tables and its totals."""
    rows = _rows(pdf)
    text = "\n".join(rows)
    invoice_no = _INVOICE_NO.search(text)
    charges = [_charge(m) for row in rows if (m := _CHARGE.match(row))]
    taxes = [_tax(m) for row in rows if (m := _TAX.match(row))]
    if invoice_no is None or not charges:
        raise UnreadableInvoice("No CMA CGM invoice with a charges table is in this file.")
    invoice = {
        "invoice_no": invoice_no.group(1),
        "invoice_date": _iso(_find(rf"(?<!Ack\. )\bDate:\s*{_DATE}", text)),
        "payable_by": _iso(_find(rf"Payable by\s+{_DATE}", text)),
        "bl_no": _find(r"Bill of Lading:\s*([A-Z0-9]{6,20})", text),
        "vessel": _find(r"Vessel:\s*(.+?)\s+Call Date", text),
        "voyage": _find(r"Voyage:\s*([A-Z0-9]{4,12})", text),
        "containers": list(dict.fromkeys(_CONTAINER.findall(_find(r"Container Number\(s\):\s*(.+)", text) or ""))),
        "usd_rate": _number(_find(r"1 USD = ([\d.]+) INR", text)),
        "charges": charges,
        "taxes": taxes,
        "total_excluding_tax": _number(_find(rf"Total Excluding Tax\s+({_MONEY})", text)),
        "total_tax": _number(_find(rf"Total GST TAX\s+({_MONEY})", text)),
        "total": _number(_find(rf"Total Including Tax\s+({_MONEY})", text) or _find(rf"Total Amount:\s*({_MONEY})", text)),
    }
    invoice["problems"] = _problems(invoice)
    return invoice


def _rows(pdf: bytes) -> list[str]:
    """Each line of each page, its pieces in reading order."""
    try:
        pages = PdfReader(io.BytesIO(pdf)).pages
    except (PdfReadError, ValueError) as exc:
        raise UnreadableInvoice(f"The file isn't a readable PDF: {exc}") from exc
    rows: list[str] = []
    for page in pages:
        pieces: list[tuple[float, float, str]] = []

        def visit(text: str, cm: list[float], tm: list[float], _font: Any, _size: Any) -> None:
            if text.strip():
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                pieces.append((y, x, text.strip()))

        page.extract_text(visitor_text=visit)
        line: list[tuple[float, float, str]] = []
        for piece in sorted(pieces, key=lambda p: -p[0]):
            if line and line[0][0] - piece[0] > ROW_HEIGHT:
                rows.append(" ".join(t for _, _, t in sorted(line, key=lambda p: p[1])))
                line = []
            line.append(piece)
        if line:
            rows.append(" ".join(t for _, _, t in sorted(line, key=lambda p: p[1])))
    return rows


def _charge(m: re.Match[str]) -> dict[str, Any]:
    return {
        "size_type": m["size"], "description": " ".join(m["description"].split()), "tax": m["tax"],
        "quantity": int(m["quantity"]), "basis": m["basis"], "rate": _number(m["rate"]), "currency": m["currency"],
        "amount": _number(m["amount"]), "amount_inr": _number(m["inr"]),
    }


def _tax(m: re.Match[str]) -> dict[str, Any]:
    return {
        "description": " ".join(m["description"].split()), "sac": m["sac"], "state": m["state"].strip(),
        "tax": m["tax"], "taxable": _number(m["taxable"]), "amount": _number(m["amount"]),
    }


def _problems(invoice: dict[str, Any]) -> list[str]:
    charges = round(sum(c["amount_inr"] for c in invoice["charges"]), 2)
    tax = round(sum(t["amount"] for t in invoice["taxes"]), 2)
    found = []
    for name, lines, stated in (
        ("charges", charges, invoice["total_excluding_tax"]),
        ("GST lines", tax, invoice["total_tax"]),
        ("charges and GST", round(charges + tax, 2), invoice["total"]),
    ):
        if stated is None:
            found.append(f"The invoice states no total for its {name}.")
        elif abs(lines - stated) > CENTS:
            found.append(f"The {name} add up to {lines:,.2f}, not the {stated:,.2f} the invoice states.")
    return found


def _find(pattern: str, text: str) -> str | None:
    found = re.search(pattern, text)
    return found.group(1).strip() if found else None


def _number(value: str | None) -> float | None:
    return float(value.replace(",", "")) if value else None


def _iso(value: str | None) -> str | None:
    return datetime.strptime(value.title(), "%d-%b-%Y").date().isoformat() if value else None
