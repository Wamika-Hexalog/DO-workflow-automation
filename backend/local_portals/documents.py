"""The documents the local portals hand out, modelled on the samples in Docs/:
the DO invoice, CMA CGM's two receipts, ODeX's security deposit slip and the eDO.

Each carries the B/L numbers as text, so the DO folder's checks match them like
the real ones. Their own numbers carry DEMO (INV-DEMO-…, RCPT-DEMO-…, EDO-DEMO-…),
so none can pass for a real document.
"""
from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=13.5)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.5, leading=11)
TITLE = ParagraphStyle("title", parent=BODY, fontName="Helvetica-Bold", fontSize=15, leading=19, alignment=TA_CENTER)


def _pdf(story: Sequence[Flowable], title: str) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=22 * mm,
        title=title,
    )
    doc.build(list(story))
    return buffer.getvalue()


def _p(text: str, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(text, style)


def _e(value: object) -> str:
    return escape("" if value is None else str(value))


def _grid(rows: list[list[object]], widths: Sequence[float], *, header: bool = True, right: Sequence[int] = ()) -> Table:
    table = Table(rows, colWidths=list(widths), hAlign="LEFT")
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#8c959f")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaeef2")),
        ]
    style += [("ALIGN", (col, 0), (col, -1), "RIGHT") for col in right]
    table.setStyle(TableStyle(style))
    return table


def _pairs(pairs: list[tuple[str, str]]) -> Table:
    """Label and value lines, for the details at the top of a document."""
    rows = [[_p(f"<b>{_e(label)}</b>"), _p(_e(value))] for label, value in pairs]
    table = Table(rows, colWidths=[42 * mm, 120 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return table


def money(value: float) -> str:
    return f"{value:,.2f}"


def day(iso: str) -> str:
    """2026-10-03 → 03-Oct-2026, as the receipts print dates."""
    try:
        return date.fromisoformat(iso[:10]).strftime("%d-%b-%Y")
    except ValueError:
        return iso


# Amounts in words, the Indian way (lakh and crore) ----------------------------

_ONES = (
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
    "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen",
)
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = [f"{_ONES[hundreds]} Hundred"] if hundreds else []
    if rest:
        parts.append(("and " if hundreds else "") + _below_hundred(rest))
    return " ".join(parts)


def _words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    for size, name in ((10**7, "Crore"), (10**5, "Lakh"), (10**3, "Thousand")):
        count, n = divmod(n, size)
        if count:
            parts.append(f"{_words(count)} {name}")
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def rupees_in_words(amount: float) -> str:
    """54720.68 → Fifty-Four Thousand Seven Hundred and Twenty Rupees and Sixty-Eight Paise Only."""
    rupees, paise = divmod(round(amount * 100), 100)
    text = f"{_words(rupees)} Rupees"
    if paise:
        text += f" and {_below_hundred(paise)} Paise"
    return text + " Only"


# The documents ----------------------------------------------------------------


def invoice_pdf(
    *, invoice_no: str, on: str, liner: str, bl_no: str, containers: Sequence[str], request_ref: str,
    lines: Sequence[tuple[str, str, float]], gst: float, total: float,
) -> bytes:
    """The liner's DO invoice for a master bill, raised from an ODeX invoice request."""
    rows: list[list[object]] = [["Description", "Basis", "Amount (INR)"]]
    rows += [[text, basis, money(amount)] for text, basis, amount in lines]
    subtotal = sum(amount for _, _, amount in lines)
    rows += [["Subtotal", "", money(subtotal)], ["GST", "18%", money(gst)], ["Total", "", money(total)]]
    charges = _grid(rows, [80 * mm, 42 * mm, 40 * mm], right=[2])
    charges.setStyle(TableStyle([("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9.5)]))
    story = [
        _p("TAX INVOICE: DELIVERY ORDER", TITLE),
        Spacer(0, 8 * mm),
        _pairs([
            ("Invoice no.", invoice_no),
            ("Liner", liner),
            ("Date", day(on)),
            ("B/L no.", bl_no),
            ("Containers", ", ".join(containers) or "As per IGM"),
            ("ODeX request", request_ref),
        ]),
        Spacer(0, 6 * mm),
        charges,
        Spacer(0, 4 * mm),
        _p(f"<b>Amount in words:</b> {_e(rupees_in_words(total))}"),
        Spacer(0, 6 * mm),
        _p(
            "Pay by RTGS/NEFT, then enter the payment against the B/L on the RTGS payments site. A security "
            "deposit of Rs. 40,000 is also due before the delivery order is released.",
            SMALL,
        ),
    ]
    return _pdf(story, f"DO invoice {invoice_no}")


def receipt_pdf(
    *, kind: str, receipt_no: str, on: str, customer: str, lines: Sequence[tuple[str, str, float]], total: float,
    utr: str, paid_on: str, account: str,
) -> bytes:
    """A payment receipt, laid out like CMA CGM's: one per RTGS confirmation, with a
    line for each invoice it pays. A security deposit's line has no invoice number."""
    rows: list[list[object]] = [["Invoice Number", "BL Number", "Invoice Amount", "Amount Received"]]
    rows += [[invoice_no or "-", bl_no, f"{money(amount)} INR", f"{money(amount)} INR"] for invoice_no, bl_no, amount in lines]
    story = [
        _p(_e(kind.upper()), TITLE),
        Spacer(0, 8 * mm),
        # As CMA CGM prints it: a line "RECEIPT", then the number (app/email_ingest.py reads it).
        _p("<b>RECEIPT</b>"),
        _p(_e(receipt_no)),
        Spacer(0, 3 * mm),
        _pairs([
            ("Date", day(on)),
            ("Received from", customer),
            ("Collected by", "RTGS/NEFT Transfer"),
        ]),
        Spacer(0, 6 * mm),
        _grid(rows, [44 * mm, 40 * mm, 38 * mm, 40 * mm], right=[2, 3]),
        Spacer(0, 5 * mm),
        _p(f"<b>RTGS/NEFT details:</b> {_e(day(paid_on))} / {_e(utr)} / {_e(account)}"),
        _p("<b>Payment mode:</b> RTGS / NEFT"),
        _p(f"<b>Amount in words:</b> {_e(rupees_in_words(total))}"),
        _p(f"<b>Total amount:</b> {_e(money(total))} INR"),
        Spacer(0, 5 * mm),
        _p("Electronic receipt: no signature or stamp required.", SMALL),
    ]
    return _pdf(story, f"Receipt {receipt_no}")


def deposit_slip_pdf(
    *, ref: str, on: str, account: str, bl_no: str, amount: float, utr: str, paid_on: str, bank: str
) -> bytes:
    """ODeX's Security Deposit Acknowledgement Slip, once the deposit is approved."""
    story = [
        _p("Security Deposit Acknowledgement Slip", TITLE),
        Spacer(0, 8 * mm),
        _pairs([("Ref no.", ref), ("Date", day(on)), ("From M/s", account)]),
        Spacer(0, 5 * mm),
        _p(
            f"Received INR {_e(money(amount))} via RTGS/NEFT towards security deposit against "
            f"<b>B/L No.: {_e(bl_no)}</b> for moving the container to destuffing point or consignee's "
            "factory for destuffing the cargo."
        ),
        Spacer(0, 3 * mm),
        _p(
            "The security deposit will be returned after the empty container is back at the empty yard, "
            "less any dues towards detention or damage."
        ),
        Spacer(0, 5 * mm),
        _grid(
            [["UTR / instrument no.", "Dated", "Amount (Rs.)", "Bank name"], [utr, day(paid_on), money(amount), bank]],
            [48 * mm, 34 * mm, 34 * mm, 46 * mm],
            right=[2],
        ),
        Spacer(0, 10 * mm),
        _p("Authorised Signatory (Import dept.)"),
        Spacer(0, 6 * mm),
        _p(
            "Note: submit the security deposit refund request on ODeX 7 days after the empty container is "
            "dropped off.",
            SMALL,
        ),
    ]
    return _pdf(story, f"Security deposit slip {ref}")


def edo_pdf(
    *, edo_no: str, on: str, valid_till: str, liner: str, account: str, bl_no: str, containers: Sequence[str],
    do_type: str, delivery_type: str, request_ref: str,
) -> bytes:
    """The delivery order, issued against an ODeX DO request."""
    story = [
        _p("DELIVERY ORDER", TITLE),
        Spacer(0, 8 * mm),
        _pairs([
            ("DO no.", edo_no),
            ("DO type", do_type),
            ("Liner", liner),
            ("Date", day(on)),
            ("Valid till", day(valid_till)),
            ("B/L no.", bl_no),
            ("Delivery", delivery_type),
            ("ODeX request", request_ref),
        ]),
        Spacer(0, 6 * mm),
        _p(
            f"To the terminal or CFS: please deliver the containers below, against B/L No. <b>{_e(bl_no)}</b>, "
            f"to M/s {_e(account)} or their order."
        ),
        Spacer(0, 4 * mm),
        _grid([["Container no."]] + [[c] for c in containers or ["As per IGM"]], [60 * mm]),
        Spacer(0, 10 * mm),
        _p("For the line, Authorised Signatory"),
    ]
    return _pdf(story, f"Delivery order {edo_no}")
