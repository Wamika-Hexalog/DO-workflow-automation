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
from reportlab.pdfgen import canvas
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
    *, invoice_no: str, on: str, bl_no: str, vessel: str, voyage: str, containers: Sequence[str],
    charges: Sequence[dict], taxes: Sequence[dict], usd_rate: float, request_ref: str,
) -> bytes:
    """The liner's DO invoice for a master bill, laid out as CMA CGM's import tax invoice:
    the charges table, the rate of exchange, the GST table and the totals, each table row
    on one line, as app/invoice_reader.py reads the real one."""
    out = io.BytesIO()
    page = canvas.Canvas(out, pagesize=A4)
    page.setTitle(f"DO invoice {invoice_no}")
    width, height = A4
    left, right = 20, width - 20
    issued = day(on).upper()
    excluding = round(sum(c["amount_inr"] for c in charges), 2)
    tax = round(sum(t["amount"] for t in taxes), 2)
    total = round(excluding + tax, 2)

    def text(x: float, y: float, value: str, size: float = 7.5, bold: bool = False, right_aligned: bool = False) -> None:
        page.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        (page.drawRightString if right_aligned else page.drawString)(x, y, value)

    def rule(y: float, x0: float = left, x1: float = right) -> None:
        page.line(x0, y, x1, y)

    page.setLineWidth(0.6)
    page.rect(left, 48, right - left, height - 68)
    for row, line in enumerate(("CMA CGM SA, C/O. CCAI *", "ONE INTERNATIONAL CENTRE", "TOWER 3 - 8TH FL SENAPATI",
                                "BAPAT MARG, ELPHINSTONE WEST", "MUMBAI/400013", "INDIA")):
        text(left + 4, height - 32 - row * 8.5, line)
    text(width / 2 - 20, height - 62, "TAX INVOICE", 15)
    page.setFillColor(colors.HexColor("#0b1f5c"))
    text(right - 12, height - 62, "CMA CGM", 19, bold=True, right_aligned=True)
    page.setFillColor(colors.HexColor("#e10a1a"))
    text(right - 12, height - 40, "DEMO", 7, bold=True, right_aligned=True)
    page.setFillColor(colors.black)
    rule(height - 92)

    top = height - 92
    page.line(width / 2, top, width / 2, top - 46)
    text(left + 4, top - 10, "Payment_info")
    text(left + 4, top - 21, f"Bill of Lading:  {bl_no}", 8)
    text(left + 4, top - 31, "Customer: 0009900123/001", 8)
    text(left + 4, top - 41, "Cust. Ref:  -")
    text(width / 2 + 6, top - 13, "IMPORT INVOICE", 11)
    text(right - 6, top - 13, "ORIGINAL*", 11, right_aligned=True)
    text(width / 2 + 6, top - 26, invoice_no, 10)
    text(width / 2 + 6, top - 41, f"Date: {issued}", 6.5)
    rule(top - 46)

    top -= 46
    page.line(width / 2, top, width / 2, top - 96)
    text(left + 4, top - 12, "Payable to:")
    text(width / 2 + 6, top - 12, "Invoice To:")
    for row, line in enumerate(("CMA CGM AGENCIES INDIA PVT LTD", "NOIDA/201301", "INDIA")):
        text(left + 70, top - 12 - row * 8.5, line)
    for row, line in enumerate(("HEXALOG GLOBAL SUPPLY CHAINS PVT LTD", "UDYOG VIHAR, GURGAON/122015", "INDIA")):
        text(width / 2 + 60, top - 12 - row * 8.5, line)
    text(width / 2 + 6, top - 60, "GSTIN:   06AAACH0000X1ZQ")
    text(left + 4, top - 60, "GSTIN:   09AAACC0000X1ZJ")
    text(left + 4, top - 72, f"ODeX request: {request_ref}")
    rule(top - 96)

    top -= 96
    text(left + 4, top - 10, f"Voyage:  {voyage}")
    text(left + 115, top - 10, "Local Voyage Ref:  -")
    text(left + 235, top - 10, f"Vessel:  {vessel}")
    text(right - 6, top - 10, f"Call Date:  {issued}", right_aligned=True)
    text(left + 4, top - 20, "Place of Receipt:  -")
    text(left + 280, top - 20, "Discharge Port:  NHAVA SHEVA")
    text(left + 4, top - 30, "Load Port:  SHEKOU")
    text(left + 280, top - 30, "Place of Delivery:  DELHI")
    text(left + 4, top - 42, f"Container Number(s):  {', '.join(containers) or 'AS PER IGM'}")
    text(left + 4, top - 52, "Quote Reference:  QSPOT-DEMO")
    text(right - 6, top - 52, f"Rate Application Date:  {issued}", right_aligned=True)
    rule(top - 56)

    top -= 56
    columns = ((left + 4, "Size/Type"), (left + 44, "Charge Description"), (left + 241, "Tax"), (left + 279, "Based on"),
               (left + 345, "Rate"), (left + 372, "Currency"), (left + 446, "Amount"), (left + 494, "Amount in INR"))
    for x, label in columns:
        text(x, top - 9, label)
    rule(top - 12)
    y = top - 21
    for charge in charges:
        text(left + 4, y, charge["size_type"])
        text(left + 34, y, "C")
        text(left + 44, y, charge["description"])
        text(left + 241, y, charge["tax"])
        text(left + 279, y, str(charge["quantity"]))
        text(left + 286, y, charge["basis"])
        text(left + 368, y, money(charge["rate"]), right_aligned=True)
        text(left + 376, y, charge["currency"])
        text(left + 470, y, money(charge["amount"]), right_aligned=True)
        text(right - 6, y, money(charge["amount_inr"]), right_aligned=True)
        y -= 10
    rule(y + 6)
    text(left + 4, y - 4, "Rate of Exchange")
    text(left + 380, y - 4, "Currency Charge Totals")
    by_currency: dict[str, float] = {}
    for charge in charges:
        by_currency[charge["currency"]] = by_currency.get(charge["currency"], 0.0) + charge["amount"]
    text(left + 4, y - 14, f"1 USD = {usd_rate:.6f} INR")
    for row, (currency, amount) in enumerate(sorted(by_currency.items(), key=lambda kv: kv[0] != "USD")):
        text(left + 380, y - 14 - row * 9, currency)
        text(left + 470, y - 14 - row * 9, money(amount), right_aligned=True)
    y -= 14 + 9 * len(by_currency)
    rule(y)
    text(left + 4, y - 10, "GST Tax applied as indicated on charges")
    text(left + 397, y - 10, "Total Excluding Tax")
    text(right - 6, y - 10, money(excluding), 8, right_aligned=True)
    rule(y - 14)

    y -= 24
    for x, label in ((left + 4, "Service Description"), (left + 169, "SAC"), (left + 206, "POS State"),
                     (left + 302, "Tax"), (left + 335, "Taxable Amt."), (left + 420, "Tax Amount")):
        text(x, y, label)
    rule(y - 3)
    y -= 12
    for line in taxes:
        text(left + 4, y, line["description"])
        text(left + 169, y, line["sac"])
        text(left + 206, y, line["state"])
        text(left + 304, y, line["tax"])
        text(left + 380, y, money(line["taxable"]), right_aligned=True)
        text(left + 460, y, money(line["amount"]), right_aligned=True)
        y -= 10
    rule(y + 6)
    text(left + 4, y - 4, "Total IGST")
    text(left + 206, y - 4, taxes[0]["state"] if taxes else "-")
    text(left + 460, y - 4, f"{money(tax)} INR", right_aligned=True)
    text(left + 416, y - 15, "Total GST TAX")
    text(right - 6, y - 15, money(tax), 8, right_aligned=True)
    text(left + 404, y - 26, "Total Including Tax")
    text(right - 6, y - 26, money(total), 8, right_aligned=True)

    rule(150)
    page.line(width / 2, 150, width / 2, 48)
    for row, line in enumerate(("(C)For and on behalf of CMA - CGM", "BOULEVARD JACQUES SAADE", "4 QUAI D'ARENC",
                                "13235 MARSEILLE-FRANCE", "Bank: DEMO BANK LTD, Account Number: DEMO-0000")):
        text(left + 4, 140 - row * 9, line)
    text(width / 2 + 40, 122, "Total Amount:", 11)
    text(right - 10, 122, f"{money(total)} INR", 11, right_aligned=True)
    text(width / 2 + 6, 108, f"Payable by {issued}", 8)
    text(width / 2 + 6, 98, "Payment before delivery of Bill Of Lading (Export) or containers (Import)", 6)
    text(left, 36, "Agent: CMA CGM Agencies (India) Private Limited. Invoice issued for and on behalf of CMA CGM SA. "
                   "Generated by the local demo portal.", 6)
    text(right, 26, "Page 1 of 1", 7, right_aligned=True)
    page.showPage()
    page.save()
    return out.getvalue()


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
