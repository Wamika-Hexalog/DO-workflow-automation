"""Hackathon shortcut: no classifier. A mail is ours only if its subject or its body has

    <prefix>:<stage>:<MBL>[:<arg>]        e.g.  DOHACK:utr:GGZ3193024:SBIN526012345678
                                                DOHACK:document:GGZ3193024:hbl:MSEC260803047251

``arg`` is the rest of the line. For ``document`` it is the folder slot to attach the next
attachment to; for ``utr`` it is the UTR, or ``deposit:<UTR>``; for ``invoice`` the invoice number.
A mail may carry several lines, one per fact.
"""
from __future__ import annotations

import html
from datetime import date
import re
from dataclasses import dataclass

STAGES = ("invoice", "utr", "document", "edo")


@dataclass(frozen=True)
class InjectedCode:
    stage: str
    mbl_no: str
    arg: str | None


def html_to_text(content: str) -> str:
    text = re.sub(r"(?i)<\s*(br|/p|/div|/tr|/li)\s*/?>", "\n", content)
    text = re.sub(r"(?i)</t[dh]\s*>", " ", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).replace("\xa0", " ")


def parse_injected_codes(body: str, *, prefix: str, subject: str = "") -> list[InjectedCode]:
    """Every valid code in the subject, then in the body, without repeats. An unknown stage is not a code.

    In the subject a code can sit anywhere (a forward adds "FW: " in front), is cut off at the next
    space, and several are separated by "|". In the body a code is a line of its own."""
    pattern = re.compile(
        rf"(?im)^[ \t>]*{re.escape(prefix)}[ \t]*:[ \t]*(?P<stage>[a-z-]+)[ \t]*:"
        r"[ \t]*(?P<mbl>[A-Z0-9]{6,20})[ \t]*(?::[ \t]*(?P<arg>\S+))?[ \t]*$"
    )
    in_subject = re.compile(
        rf"(?i)(?<![A-Za-z0-9]){re.escape(prefix)}:(?P<stage>[a-z-]+):(?P<mbl>[A-Z0-9]{{6,20}})(?::(?P<arg>[^\s|]+))?"
    )
    found = [*in_subject.finditer(subject), *pattern.finditer(html_to_text(body))]
    codes: list[InjectedCode] = []
    for match in found:
        stage = match.group("stage").lower()
        code = InjectedCode(stage=stage, mbl_no=match.group("mbl").upper(), arg=match.group("arg"))
        if stage in STAGES and code not in codes:
            codes.append(code)
    return codes


def fields_for(code: InjectedCode) -> dict[str, str]:
    """Shipment fields a code fills when they are empty: utr -> payment_utr, utr with
    "deposit:<utr>" -> deposit_utr, invoice -> do_invoice_no. A UTR also gives the date
    of transfer: the day it comes in, until someone types the real one."""
    if not code.arg:
        return {}
    if code.stage == "utr":
        kind, _, utr = code.arg.partition(":")
        found = {"deposit_utr": utr} if kind == "deposit" and utr else {"payment_utr": code.arg}
        return {**found, "payment_date": date.today().isoformat()}
    if code.stage == "invoice":
        return {"do_invoice_no": code.arg}
    return {}
