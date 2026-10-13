"""The letters and forms the app writes itself, drawn as PDFs with reportlab.

Each one follows the team's Word original. Values arrive already formatted;
anything typed by a user is escaped before it reaches a Paragraph.
"""
from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Flowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

PAGE_W, PAGE_H = A4
MARGIN = 22 * mm
PURPLE = colors.HexColor("#7030A0")
STAMP_WIDTH = 34 * mm

BODY = ParagraphStyle("body", fontName="Times-Roman", fontSize=11, leading=14.5)
JUSTIFIED = ParagraphStyle("justified", parent=BODY, alignment=TA_JUSTIFY)
RIGHT = ParagraphStyle("right", parent=BODY, alignment=TA_RIGHT)
CENTRE = ParagraphStyle("centre", parent=BODY, alignment=TA_CENTER)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=9, leading=11.5)


class MissingAsset(FileNotFoundError):
    """A stamp or letterhead image is not where do_folder.yaml says."""


@dataclass(frozen=True)
class LetterInput:
    printed: dict[str, str]  # every field, formatted for print
    raw: dict[str, Any]  # every field as stored, for ticks on the checklist
    hbls: list[str]
    stamp: Path | None
    assets_dir: Path


def _p(text: str, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(text, style)


def _e(value: str) -> str:
    return escape(value or "")


def _lines(*lines: str, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph("<br/>".join(lines), style)


def _image(path: Path) -> ImageReader:
    if not path.is_file():
        raise MissingAsset(f"{path} is missing. Run `make assets` to build it from the Docs folder.")
    return ImageReader(str(path))


class StampMark(Flowable):
    """Takes no space; draws the stamp over the text around it, like a rubber stamp."""

    def __init__(self, image: ImageReader, dx: float, dy: float, width: float = STAMP_WIDTH):
        super().__init__()
        self.image, self.dx, self.dy, self.stamp_w = image, dx, dy, width

    def wrap(self, avail_width, avail_height):
        return 0, 0

    def draw(self):
        image_w, image_h = self.image.getSize()
        height = self.stamp_w * image_h / image_w
        self.canv.drawImage(self.image, self.dx, self.dy - height / 2, self.stamp_w, height, mask="auto")


def _stamp(data: LetterInput, dx: float, dy: float) -> list[Flowable]:
    return [StampMark(_image(data.stamp), dx, dy)] if data.stamp else []


# Letterheads --------------------------------------------------------------


def _hexalog_page(canvas: Canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(PURPLE)
    canvas.setFont("Times-Bold", 19)
    canvas.drawCentredString(PAGE_W / 2, PAGE_H - 22 * mm, "HEXALOG GLOBAL SUPPLY CHAINS PVT LTD")
    y = 30 * mm
    canvas.setFont("Times-Bold", 11)
    canvas.drawString(MARGIN, y, "HEXALOG GLOBAL SUPPLY CHAINS PVT LTD")
    canvas.setFont("Times-Roman", 9.5)
    for line in (
        "(Formerly Known as Step Forward Logistics Pvt. Ltd) CIN: U63030DL2022PTC407362",
        "REGISTERED OFFICE: Plot Nos 92 – 93 AIHP Millenium Udyog Vihar Phase – 4, Gurugram, Haryana - 122015",
        "(INDIA), Tel: +91-9311855418. EMAIL – Support@hexalog.in",
    ):
        y -= 4.3 * mm
        canvas.drawString(MARGIN, y, line)
    canvas.restoreState()


def _jsr_page(assets_dir: Path) -> Callable[[Canvas, Any], None]:
    header = _image(assets_dir / "letterheads" / "jsr_header.jpg")
    footer = _image(assets_dir / "letterheads" / "jsr_footer.jpg")

    def draw(canvas: Canvas, doc) -> None:
        width = PAGE_W - 2 * 14 * mm
        for image, y_top in ((header, PAGE_H - 12 * mm), (footer, 22 * mm)):
            image_w, image_h = image.getSize()
            height = width * image_h / image_w
            canvas.drawImage(image, 14 * mm, y_top - height, width, height, mask="auto")

    return draw


def _build(story: list[Flowable], on_page, top: float = 38 * mm, bottom: float = 42 * mm) -> bytes:
    buffer = io.BytesIO()
    template = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=top, bottomMargin=bottom
    )
    template.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()


# Shared blocks ------------------------------------------------------------


def _addressee(v: dict[str, str]) -> Paragraph:
    return _lines("To:", _e(v["line_agent"]), _e(v["line_agent_address"]))


def _shipment_block(data: LetterInput, with_cha: bool = False) -> Paragraph:
    v = data.printed
    lines = [f"Master B/L No. {_e(v['mbl_no'])}", f"Contr – {_e(v['containers'])}"]
    lines += [f"HB/L No. – {_e(hbl)}" for hbl in data.hbls]
    lines += [
        f"Vessel Name – {_e(v['vessel'])}",
        f"Voyage – {_e(v['voyage'])}",
        f"Cnee as per MBL – {_e(v['consignee_mbl'])}",
        f"Cnee as per HBL – {_e(v['consignee_hbl'])}.",
    ]
    if with_cha:
        lines.append(f"CHA – {_e(v['cha'])}")
    return _lines(*lines)


_NOC_BODY = [
    "We hereby confirm that we have no objection if you issue the delivery order / factory destuffing "
    "permission to the bearer of this letter as per IGM filed by the carrier.",
    "Please find enclosed herewith CMA CGM Original B/L duly endorsed (if negotiable), a copy of MBL duly "
    "attested (if surrendered/waybill/Telex), a copy of HBL(s), a copy of our LOI and original LOI of "
    "ultimate consignee. Further we confirm that we have collected all our dues against this shipment from "
    "ultimate consignee and if there will be any due against above said shipment (till empty return) which "
    "is not cleared by the ultimate consignee, we will clear that dues being consignee on MBL and same has "
    "also been confirmed in our LOI too.",
]


def _noc_story(data: LetterInput) -> list[Flowable]:
    v = data.printed
    return [
        _p(f"Date – {_e(v['letter_date'])}"),
        Spacer(0, 5 * mm),
        _addressee(v),
        Spacer(0, 5 * mm),
        _p(
            "SUBJECT - NO Objection Letter to issue the Delivery Order for destuffing the cargo either at ICD "
            "or at consignee’s premise for –"
        ),
        Spacer(0, 3 * mm),
        _shipment_block(data),
        Spacer(0, 5 * mm),
        _p("Dear Sir,"),
        _p(_NOC_BODY[0], JUSTIFIED),
        Spacer(0, 3 * mm),
        _p(_NOC_BODY[1], JUSTIFIED),
        Spacer(0, 8 * mm),
    ]


def _hexalog_signature(data: LetterInput) -> list[Flowable]:
    v = data.printed
    block = _lines(
        "Thanking You,",
        f"For {_e(v['forwarder_name'])}",
        "<br/>Auth. Sign.",
        f"Name – {_e(v['forwarder_signatory'])}",
        f"Designation – {_e(v['forwarder_designation'])}",
    )
    # Beside the name, clear of the "For ..." line, as the team stamps it by hand.
    return [KeepTogether([block, *_stamp(data, 84 * mm, 10 * mm)])]


# The documents ------------------------------------------------------------


def noc_cha(data: LetterInput) -> bytes:
    v = data.printed
    signature = _lines("Thanks / Regards", f"For {_e(v['cha'])}", "<br/><br/>(Authorised Signatory)")
    story = _noc_story(data) + [KeepTogether([signature, *_stamp(data, 6 * mm, 12 * mm)])]
    return _build(story, _jsr_page(data.assets_dir), top=40 * mm, bottom=36 * mm)


def endorsed_do(data: LetterInput) -> bytes:
    v = data.printed
    story = [
        _p(f"Date – {_e(v['letter_date'])}", RIGHT),
        Spacer(0, 6 * mm),
        _addressee(v),
        Spacer(0, 10 * mm),
        _p("Sub: Issue of Delivery order", CENTRE),
        Spacer(0, 8 * mm),
        _p("Dear Sir,"),
        _p(
            f"We hereby authorize {_e(v['do_collector'])} to collect Delivery order of M/S "
            f"{_e(v['consignee_hbl'])} from your office. The necessary charges would be paid by them to you. "
            "Kindly hand over the delivery order to them.",
            ParagraphStyle("indented", parent=JUSTIFIED, firstLineIndent=12 * mm),
        ),
        Spacer(0, 10 * mm),
        _lines(f"M BL NO- {_e(v['mbl_no'])}", f"Contr – {_e(v['containers'])}", style=CENTRE),
        Spacer(0, 14 * mm),
        KeepTogether([_p("Thanking You,"), *_stamp(data, 10 * mm, 4 * mm)]),
    ]
    return _build(story, _jsr_page(data.assets_dir), top=40 * mm, bottom=36 * mm)


def noc_hexalog(data: LetterInput) -> bytes:
    story = _noc_story(data) + _hexalog_signature(data)
    return _build(story, _hexalog_page)


def authorization(data: LetterInput) -> bytes:
    v = data.printed
    cha = _e(v["cha"])
    story = [
        _p(f"Date – {_e(v['letter_date'])}", RIGHT),
        Spacer(0, 5 * mm),
        _addressee(v),
        Spacer(0, 7 * mm),
        _p(f"SUB: <u>AUTHORIZE TO CHA - {cha}</u>"),
        Spacer(0, 4 * mm),
        _shipment_block(data, with_cha=True),
        Spacer(0, 7 * mm),
        _p("Dear Sir,"),
        _p(
            f"We hereby authorize our CHA - <b>{cha}</b> to apply the DO request on our behalf to collect "
            f"Delivery order of M/S {_e(v['consignee_hbl'])}. Kindly release the Delivery order to them.",
            ParagraphStyle("indented", parent=JUSTIFIED, firstLineIndent=12 * mm),
        ),
        Spacer(0, 14 * mm),
        *_hexalog_signature(data),
    ]
    return _build(story, _hexalog_page)


class _Box(Flowable):
    """A tick box, ticked or not, for the checklist."""

    size = 9

    def __init__(self, ticked: bool):
        super().__init__()
        self.ticked = ticked

    def wrap(self, avail_width, avail_height):
        return self.size, self.size

    def draw(self):
        c = self.canv
        c.setLineWidth(0.8)
        c.rect(0, 0, self.size, self.size)
        if self.ticked:
            c.setLineWidth(1.6)
            path = c.beginPath()
            path.moveTo(1.8, 4.6)
            path.lineTo(3.8, 2.0)
            path.lineTo(7.6, 7.6)
            c.drawPath(path, stroke=1, fill=0)


def checklist(data: LetterInput) -> bytes:
    v, raw = data.printed, data.raw
    banner = _image(data.assets_dir / "letterheads" / "cma_checklist_banner.jpg")
    office = _image(data.assets_dir / "letterheads" / "cma_office_use.jpg")
    margin = 16 * mm  # the form is wider than a letter
    width = PAGE_W - 2 * margin

    def picture(image: ImageReader) -> Flowable:
        image_w, image_h = image.getSize()
        return _Picture(image, width, width * image_h / image_w)

    def row(label: str, choices: list[tuple[str, bool]] = (), value: str = "") -> Table:
        """One line of the form: the question, then each option followed by its box.
        Plain strings, so the table sizes each column to its text and nothing wraps."""
        cells: list[Any] = [label]
        widths: list[float | None] = [68 * mm]
        for text, ticked in choices:
            # Each box sits right after its own label, with a gap before the next option.
            cells += [text, _Box(ticked)]
            widths += [stringWidth(text, "Times-Roman", 10.5) + 6, 12 * mm]
        if value:
            cells.append(value)
            widths.append(None)
        line = Table([cells], colWidths=widths, hAlign="LEFT")
        line.setStyle(
            TableStyle(
                [
                    ("FONT", (0, 0), (-1, -1), "Times-Roman", 10.5),
                    ("FONT", (-1, 0), (-1, 0), "Times-Bold" if value else "Times-Roman", 10.5),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 1),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        return line

    def choice(key: str, text: str) -> tuple[str, bool]:
        return text, raw.get(key) == text

    hbls = ", ".join(data.hbls)
    ticks = [
        row("1) Endorsed :", [choice("endorsed", t) for t in ("OBL", "Waybill", "TELEX Release")]),
        row("2) Delivery Type:", [choice("delivery_type", t) for t in ("Factory De-stuff", "CFS De-stuff", "LCL", "DPD")]),
        row("3) LOI for Factory Delivery:", [choice("loi_for", t) for t in ("MBL Consignee", "HBL Consignee")]),
        row("4) Factory location", value=v["factory_location"]),
        row("5) Date of validity Required", value=v["validity_required"]),
        row("6) NOC From Forwarder for HBL Shipments:", [("", raw.get("noc_from_forwarder") is True)]),
        row("7) High Seas Sale", [("HSS", raw.get("hss") is True), ("Documents & LOI :", raw.get("hss_documents") is True)]),
        row("8) Mode of Payment:", [choice("payment_mode", t) for t in ("Online Payment", "DD/PAYORDER", "CHEQUE")]),
        row("9)", [choice("do_type", t) for t in ("D/O", "E-DO")]),
    ]

    deposits = Table(
        [
            ["Sr. No.", "List of Cargo", "Security Deposit Per Teu (INR)"],
            ["1", "Naphtha /Naphthalene", "25,000"],
            ["2", "Clove", "25,000"],
            ["3", "Granite/Marbles", "25,000"],
            ["4", "Coal tar or mineral tar pitch", "25,000"],
            ["5", "Batteries and battery scrap", "40,000"],
        ],
        colWidths=[16 * mm, 50 * mm, 52 * mm],
        hAlign="LEFT",
    )
    deposits.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Times-Roman", 9),
                ("FONT", (0, 0), (-1, 0), "Times-Bold", 9),
                ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (2, 0), (2, -1), "CENTER"),
            ]
        )
    )

    notes = [
        "Please fill all relevant content of checklist for smooth issuance of Delivery order.",
        "Separate token to be taken for each BL/HBL if wherever token management system installed.",
        "Documents will be accepted between 09.00 AM to 4.00 PM (MON-FRI)",
        "Please check delivery order for CFS point, Empty validity, Empty or Gang letter upon receipt of "
        "delivery order.",
        "<u>SECURITY DEPOSIT AGAINST SPECIFIC COMMODITIES (in form of NEFT/RTGS)</u>",
    ]
    story = [
        picture(banner),
        Spacer(0, 2 * mm),
        picture(office),
        Spacer(0, 4 * mm),
        _p(f"Date&nbsp;&nbsp;&nbsp;<b>{_e(v['letter_date'])}</b>", RIGHT),
        Spacer(0, 3 * mm),
        _lines(
            f"Consignee Name: <b>{_e(v['consignee_mbl'])}</b>&nbsp;&nbsp; Tel No.: {_e(v['forwarder_tel'])}",
            f"Consignee Email ID: {_e(v['forwarder_email'])}",
            f"CHA Name: <b>{_e(v['cha'])}</b>",
            f"CHA Email ID: {_e(v['cha_email'])}",
        ),
        Spacer(0, 4 * mm),
        _p(f"MBL /HBL No&nbsp;&nbsp;<b>{_e(v['mbl_no'])}</b>" + (f" / {_e(hbls)}" if hbls else "")),
        Spacer(0, 4 * mm),
        _p("<b>(Please tick the appropriate box)</b>", CENTRE),
        Spacer(0, 2 * mm),
        *ticks,
        Spacer(0, 6 * mm),
        _p("<b>**Special note for customer**</b>", CENTRE),
        Spacer(0, 2 * mm),
        *[_p(f"<b>{i}. {note}</b>", SMALL) for i, note in enumerate(notes, start=1)],
        Spacer(0, 3 * mm),
        deposits,
    ]
    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=12 * mm, bottomMargin=12 * mm
    ).build(story)
    return buffer.getvalue()


class _Picture(Flowable):
    def __init__(self, image: ImageReader, width: float, height: float):
        super().__init__()
        self.image, self.pic_w, self.pic_h = image, width, height

    def wrap(self, avail_width, avail_height):
        return self.pic_w, self.pic_h

    def draw(self):
        self.canv.drawImage(self.image, 0, 0, self.pic_w, self.pic_h, mask="auto")


RENDERERS: dict[str, Callable[[LetterInput], bytes]] = {
    "noc_cha": noc_cha,
    "endorsed_do": endorsed_do,
    "noc_hexalog": noc_hexalog,
    "authorization": authorization,
    "checklist": checklist,
}
