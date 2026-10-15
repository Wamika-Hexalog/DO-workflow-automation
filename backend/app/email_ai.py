"""Reads a marked mail that no rule in email_infer.py knows, with Gemini, so a new wording
still moves the job on: Finance's own reply, an eDO mail, a new forwarder's pre-alert.

The model only names the kind of mail and copies the identifiers it states. This module
turns that into the same InjectedCode list the rules give, so everything after it is the
same. An identifier the mail doesn't contain is dropped: the model copies, it never guesses.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx

from . import pdfs
from .config import Settings
from .email_code import InjectedCode, html_to_text

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# A model that is retired, rate-limited or busy: the next model in the setting reads instead.
_TRY_NEXT = (404, 429, 500, 503)
# Gemini's busy spells are short: after a pause, each model is tried once more.
ROUNDS, PAUSE_SECONDS = 2, 3
# A busy model can hold a request for half a minute before it answers 503: the next one reads instead.
MODEL_SECONDS = 10
BODY_CHARS = 12000  # a long forwarded thread is cut here
ATTACHMENT_CHARS = 3000  # the first page of an attachment is enough

# Each kind the model may name: what the job's history calls it, and what it is.
KINDS = {
    "pre_alert": ("a pre-alert", "the forwarder's pre-alert: the draft MBL and the HBLs of a shipment"),
    "invoice_released": ("ODeX's invoice release", "ODeX or CMA CGM says the DO invoice for a B/L is released"),
    "finance_payment": (
        "Finance's payment reply",
        "Finance says it paid CMA CGM, with the UTR of the DO invoice payment, of the security deposit, or both",
    ),
    "rtgs_receipt": (
        "CMA CGM's RTGS receipt",
        "CMA CGM confirms an RTGS/NEFT payment: its receipt for the DO invoice or for the security deposit",
    ),
    "security_slip": ("ODeX's security slip", "ODeX's security deposit slip: the deposit for a house B/L is approved"),
    "edo_issued": ("CMA CGM's eDO", "CMA CGM issued the delivery order (eDO) for a B/L"),
    "other": ("none of the DO mails", "anything else"),
}

INSTRUCTIONS = """You read mails for the customs desk of Hexalog, a freight forwarder in India, which gets
delivery orders (DO) from the shipping line CMA CGM for import shipments. Say which kind of mail
this is, and copy the identifiers it states exactly as written. Leave a field empty when the
mail doesn't state it. Never guess or build an identifier.

Kinds:
{kinds}

Fields:
- mbl_no: the master bill of lading (MBL) number.
- hbl_nos: the house bill of lading (HBL) numbers.
- invoice_nos: CMA CGM's DO invoice numbers.
- payment_utr: the bank's UTR (reference number) of the DO invoice payment.
- deposit_utr: the bank's UTR of the security deposit payment. Finance often pays both in one
  mail and gives a UTR for each: fill both.
- deposit_receipt: for rtgs_receipt only: true when the receipt is for the security deposit, not an invoice.
- reason: one short sentence on why the mail is that kind."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "kind": {"type": "STRING", "enum": list(KINDS)},
        "mbl_no": {"type": "STRING"},
        "hbl_nos": {"type": "ARRAY", "items": {"type": "STRING"}},
        "invoice_nos": {"type": "ARRAY", "items": {"type": "STRING"}},
        "payment_utr": {"type": "STRING"},
        "deposit_utr": {"type": "STRING"},
        "deposit_receipt": {"type": "BOOLEAN"},
        "reason": {"type": "STRING"},
    },
    "required": ["kind", "mbl_no", "hbl_nos", "invoice_nos", "payment_utr", "deposit_utr", "deposit_receipt", "reason"],
}

_IDENTIFIER = re.compile(r"[A-Z0-9][A-Z0-9/-]{3,29}")
_SLOTS = {"security_receipt": "the Security Receipt", "do_receipt": "the DO Receipt"}


class ReaderError(Exception):
    """Gemini could not be asked, or its answer could not be read."""


async def read(settings: Settings, subject: str, body: str, files: list[dict[str, str]]) -> dict[str, Any]:
    """What the mail is and the codes it gives, with a line for the job's history."""
    texts = [(f["name"], await asyncio.to_thread(_text_of, Path(f["path"]), f["name"])) for f in files]
    body_text = html_to_text(body)
    prompt = "\n\n".join(
        [f"Subject: {subject}", f"Body:\n{body_text[:BODY_CHARS]}"]
        + [f"Attachment {name}:\n{text[:ATTACHMENT_CHARS]}" for name, text in texts]
    )
    reading, model = await _ask(settings, prompt)
    mail_text = "\n".join([subject, body_text, *(f"{name}\n{text}" for name, text in texts)])
    codes = codes_from(reading, mail_text)
    return {
        "kind": reading.get("kind", "other"),
        "model": model,
        "codes": [{"stage": c.stage, "mbl_no": c.mbl_no, "arg": c.arg} for c in codes],
        "note": _note(model, reading, codes),
    }


def codes_from(reading: dict[str, Any], mail_text: str) -> list[InjectedCode]:
    """The codes the rules would give for this kind of mail, from what the model read.
    An identifier that isn't in the mail is left out."""
    flat = re.sub(r"\s+", "", mail_text).upper()

    def stated(value: Any) -> str:
        found = re.sub(r"\s+", "", str(value or "")).upper()
        return found if _IDENTIFIER.fullmatch(found) and found in flat else ""

    kind = reading.get("kind")
    mbl = stated(reading.get("mbl_no"))
    hbls = [h for h in map(stated, reading.get("hbl_nos") or []) if h]
    invoices = [n for n in map(stated, reading.get("invoice_nos") or []) if n]
    payment, deposit = stated(reading.get("payment_utr")), stated(reading.get("deposit_utr"))
    bl = mbl or next(iter(hbls), "")
    if not bl:
        return []
    if kind == "pre_alert" and mbl:
        return [InjectedCode("document", mbl, None)]
    if kind == "invoice_released":
        return [InjectedCode("invoice", bl, number) for number in invoices]
    if kind == "finance_payment":
        return [InjectedCode("utr", bl, utr) for utr in (payment, f"deposit:{deposit}" if deposit else "") if utr]
    if kind == "rtgs_receipt" and reading.get("deposit_receipt"):
        utr = deposit or payment
        return [*([InjectedCode("utr", bl, f"deposit:{utr}")] if utr else []), InjectedCode("document", bl, "security_receipt")]
    if kind == "rtgs_receipt":
        return [
            *([InjectedCode("utr", bl, payment)] if payment else []),
            *[InjectedCode("invoice", bl, number) for number in invoices[:1]],
            InjectedCode("document", bl, "do_receipt"),
        ]
    if kind == "security_slip":
        return [InjectedCode("document", hbl, f"security_odex_receipt:{hbl}") for hbl in hbls]
    if kind == "edo_issued":
        return [InjectedCode("edo", bl, None)]
    return []


async def _ask(settings: Settings, prompt: str) -> tuple[dict[str, Any], str]:
    """The model's reading, and which model gave it."""
    kinds = "\n".join(f"- {name}: {about}" for name, (_, about) in KINDS.items())
    request = {
        "systemInstruction": {"parts": [{"text": INSTRUCTIONS.format(kinds=kinds)}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": SCHEMA},
    }
    models = [m.strip() for m in settings.mail_ai_model.split(",") if m.strip()]
    tried = []
    async with httpx.AsyncClient(timeout=MODEL_SECONDS) as client:
        for model in models * ROUNDS:
            if tried and model == models[0]:
                await asyncio.sleep(PAUSE_SECONDS)
            try:
                response = await client.post(API.format(model=model), headers={"x-goog-api-key": settings.google_api_key}, json=request)
            except httpx.TimeoutException:
                tried.append(f"{model} timed out")
                continue
            if response.status_code == 200:
                return _reading(response), model
            if response.status_code not in _TRY_NEXT:
                raise ReaderError(f"{model} answered {response.status_code}: {response.text[:300]}")
            tried.append(f"{model} answered {response.status_code}")
    raise ReaderError(f"No Gemini model could read the mail: {'; '.join(tried) or 'none is set'}.")


def _reading(response: httpx.Response) -> dict[str, Any]:
    try:
        parts = response.json()["candidates"][0]["content"]["parts"]
        reading = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ReaderError(f"Gemini's answer could not be read: {exc}") from exc
    if not isinstance(reading, dict) or reading.get("kind") not in KINDS:
        raise ReaderError(f"Gemini named no known kind of mail: {str(reading)[:200]}")
    return reading


def _text_of(path: Path, name: str) -> str:
    try:
        return pdfs.extract_text(pdfs.to_pdf(path.read_bytes(), name))
    except pdfs.UnreadableFile:
        return ""


def _note(model: str, reading: dict[str, Any], codes: list[InjectedCode]) -> str:
    """The job's history line. UTRs show their last 4 digits only."""
    facts = []
    for code in codes:
        if code.stage == "utr":
            kind, _, utr = (code.arg or "").rpartition(":")
            facts.append(f"{'deposit' if kind == 'deposit' else 'invoice'} UTR …{utr[-4:]}")
        elif code.stage == "invoice":
            facts.append(f"invoice {code.arg}")
        elif code.stage == "edo":
            facts.append(f"eDO issued for {code.mbl_no}")
        elif code.arg:
            slot, _, hbl = code.arg.partition(":")
            facts.append(f"its file goes to {_SLOTS.get(slot, f'the Security-cum-ODEX Receipt of HBL {hbl}')}")
        else:
            facts.append(f"MBL {code.mbl_no}")
    label = KINDS[reading.get("kind", "other")][0]
    reason = str(reading.get("reason") or "").strip()[:200]
    found = f": {', '.join(facts)}" if facts else ""
    return f"No rule knew this mail's wording, so {model} read it as {label}{found}. {reason}".strip()
