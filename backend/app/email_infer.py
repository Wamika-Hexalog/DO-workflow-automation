"""Reads the facts out of the three kinds of mail the demo uses, so a mail needs only the short
marker (config: mail_marker, default "#DOHACK") and no code lines. Each recognizer returns the same
InjectedCode list as an explicit code, so the workflow downstream is the same.

    CMA CGM RTGS/NEFT confirmation  -> UTR, invoice number (or the deposit), the receipt
    ODeX security slip              -> the slip, for the HBL it names
    draft MBL/HBL mail              -> the MBL (its attachments are routed by file name or text)

shortcut: written from the sample mails only; a changed wording needs a changed pattern here.
"""
from __future__ import annotations

import re

from .email_code import InjectedCode, html_to_text

_RTGS_ROW = re.compile(r"(\S+)\s+([A-Z]{3}\d{7})\s+[IE]\s+([\d,]+\.\d\d)")
_MBL = re.compile(r"\b[A-Z]{3}\d{7}\b")


def marker_present(marker: str, subject: str, body: str) -> bool:
    """An empty marker means any mail may be read; the recognizers are strict anyway."""
    return not marker or re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", f"{subject}\n{body}", re.I) is not None


def infer_codes(subject: str, body: str, *, hbl_pattern: str) -> list[InjectedCode]:
    text = f"{subject}\n{html_to_text(body)}"
    if "RTGS/NEFT" in text and "Request Confirmed" in text:
        return _rtgs(text)
    if re.search(r"security slip", text, re.I) and "Deposit Approved" in text:
        return _slip(text)
    return _drafts(text, hbl_pattern)


def _rtgs(text: str) -> list[InjectedCode]:
    utr, row = re.search(r"UTR:\s*(\d{8,22})", text), _RTGS_ROW.search(text)
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


def _drafts(text: str, hbl_pattern: str) -> list[InjectedCode]:
    if "MBL" not in text or "HBL" not in text or not re.search(hbl_pattern, text):
        return []
    return [InjectedCode("document", mbl, None) for mbl in dict.fromkeys(_MBL.findall(text))][:3]
