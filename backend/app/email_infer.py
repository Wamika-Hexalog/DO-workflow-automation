"""Reads the facts out of the three kinds of mail the demo uses, so a mail needs only the short
marker (config: mail_marker, default "#DOHACK") and no code lines. Each recognizer returns the same
InjectedCode list as an explicit code, so the workflow downstream is the same.

    CMA CGM RTGS/NEFT confirmation  -> UTR, invoice number (or the deposit), the receipt
    ODeX security slip              -> the slip, for the HBL it names
    ODeX "Invoices Received"        -> each invoice the liner released for the B/L
    Finance's reply to the app      -> the UTR of the invoice payment and of the deposit
    draft MBL/HBL mail              -> the MBL (its attachments are routed by file name or text)

shortcut: written from the sample mails only; a changed wording needs a changed pattern here.
"""
from __future__ import annotations

import re

from .email_code import InjectedCode, html_to_text

_RTGS_ROW = re.compile(r"(\S+)\s+([A-Z]{3}\d{7})\s+[IE]\s+([\d,]+\.\d\d)")
_MBL = re.compile(r"\b[A-Z]{3}\d{7}\b")
_MBL_IN = re.compile(r"\b([A-Z]{3}\d{7})\b")
# The lines the app's payment mail asks Finance to fill (app/process.py). Left empty, as
# in the copy quoted under a reply, they match nothing.
# ODeX's notice that the liner released the invoices for a B/L, with one row per invoice:
# number, type, amount, bill-to party. Each cell is a block of its own, so a row spans lines.
_ODEX_RELEASED = re.compile(r"\bInvoices?\s+(?:released|received)\s+for\s+([A-Z0-9]{6,20})\b", re.I)
_ODEX_INVOICE_ROW = re.compile(
    r"\b((?=[A-Z0-9-]*\d)[A-Z][A-Z0-9-]{5,24})\s+((?:NON\s+)?FREIGHT|[A-Z][A-Z ]{2,30}?)\s+([\d,]+\.\d{2})\b"
)
_FINANCE_UTR = re.compile(r"\b((?i:invoice|deposit)) (?i:utr):[ \t]*((?=[A-Z0-9]*\d)[A-Z0-9]{8,22})\b")


def marker_present(marker: str, subject: str, body: str) -> bool:
    """An empty marker means any mail may be read; the recognizers are strict anyway."""
    return not marker or re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", f"{subject}\n{body}", re.I) is not None


def infer_codes(subject: str, body: str, *, hbl_pattern: str) -> list[InjectedCode]:
    text = f"{subject}\n{html_to_text(body)}"
    if "RTGS/NEFT" in text and "Request Confirmed" in text:
        return _rtgs(text)
    if re.search(r"security slip", text, re.I) and "Deposit Approved" in text:
        return _slip(text)
    if _ODEX_RELEASED.search(text):
        return _odex_invoices(text)
    if _FINANCE_UTR.search(text):
        return _finance(text)
    return _drafts(text, hbl_pattern)


def _rtgs(text: str) -> list[InjectedCode]:
    # A UTR may carry letters (SBIN526012345678), not only digits.
    utr, row = re.search(r"UTR:\s*([A-Z0-9]{8,22})\b", text), _RTGS_ROW.search(text)
    if not utr or not row:
        return []
    invoice_ref, mbl = row.group(1), row.group(2)
    if invoice_ref == "-":  # no invoice: this is the security deposit
        return [
            InjectedCode("utr", mbl, f"deposit:{utr.group(1)}"),
            InjectedCode("document", mbl, "security_receipt"),
        ]
    return [
        InjectedCode("utr", mbl, utr.group(1)),
        InjectedCode("invoice", mbl, invoice_ref),
        InjectedCode("document", mbl, "do_receipt"),
    ]


def _slip(text: str) -> list[InjectedCode]:
    hbl = re.search(r"BL No\.\s*-\s*([A-Z0-9]+)", text)
    # The slip names the house bill, not the MBL; the job is found by its HBL.
    return [InjectedCode("document", hbl.group(1), f"security_odex_receipt:{hbl.group(1)}")] if hbl else []


def _odex_invoices(text: str) -> list[InjectedCode]:
    bl = _ODEX_RELEASED.search(text).group(1).upper()
    numbers = dict.fromkeys(row.group(1) for row in _ODEX_INVOICE_ROW.finditer(text))
    return [InjectedCode("invoice", bl, number) for number in numbers]


def _finance(text: str) -> list[InjectedCode]:
    # The payment mail's subject names the MBL: "<job> // <MBL> // CMA //".
    mbl = re.search(r"\bMBL\s+([A-Z]{3}\d{7})\b", text) or _MBL_IN.search(text)
    if not mbl:
        return []
    found = dict.fromkeys((kind.lower(), utr) for kind, utr in _FINANCE_UTR.findall(text))
    return [
        InjectedCode("utr", mbl.group(1), utr if kind == "invoice" else f"deposit:{utr}") for kind, utr in found
    ]


def _drafts(text: str, hbl_pattern: str) -> list[InjectedCode]:
    if "MBL" not in text or "HBL" not in text or not re.search(hbl_pattern, text):
        return []
    return [InjectedCode("document", mbl, None) for mbl in dict.fromkeys(_MBL.findall(text))][:3]
