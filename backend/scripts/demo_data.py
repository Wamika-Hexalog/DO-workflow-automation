"""Demo data: removes every job except the one the team ran end to end, then makes
dummy jobs that look like real ones, one at each step of the process, several with a
problem for the assistant to explain.

Every record comes from the app's own code: the mail workflow reads each mail, the
process jobs record each portal step, the local CMA CGM answers each request and writes
its documents, and the one failed browser run is a real run against a stopped ODeX.

Stop the demo first (the app and the local portals), fill Finance's email in the fixed
details, then:

    cd backend && uv run python scripts/demo_data.py [--keep HXL/IMP/26-27/0439]

It keeps the job named by --keep. If that job isn't in the database, it keeps none, and the
dummy jobs take the numbers below it, so the shipment run end to end next gets that number.
Times count back from when it runs, so run it shortly before the demo. It first copies the
database, the files and the local portals' state to data/backups/<time>/.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import random
import secrets
import shutil
import socket
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inngest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from inngest.experimental import mocked  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402
from reportlab.lib.colors import Color  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402
from sqlalchemy import delete, func, select, update  # noqa: E402

from app import containers, portal_params, process, service, tasks  # noqa: E402
from app.config import BACKEND_DIR, Settings  # noqa: E402
from app.db import (  # noqa: E402
    AutomationRun, Document, Event, IdempotencyKey, InboundMail, Profile, Shipment, Task, make_sessionmaker,
)
from app.auth import current_user  # noqa: E402
from app.main import create_app  # noqa: E402
from app.seed import PROFILE_ID  # noqa: E402
from app.workflows.events import EventNames  # noqa: E402
from local_portals import cma  # noqa: E402
from local_portals import documents as portal_docs  # noqa: E402
from local_portals import store as portal_store  # noqa: E402
from local_portals.settings import data_dir as portal_data_dir  # noqa: E402

KEEP_JOB = "HXL/IMP/26-27/0439"  # the job the team ran end to end on 15 Oct 2026; --keep changes it
OPS = "customs-ops@hexalog.in"  # the team forwards each mail from its own mailbox
# The deposit on ODeX needs the account the deposit is refunded into. Dummy values,
# set only where the fixed details have none.
DUMMY_REFUND = {
    "refund_bank": "ICICI BANK", "refund_branch": "NEHRU PLACE, NEW DELHI",
    "refund_account_no": "004105012345", "refund_ifsc": "ICIC0000041",
}
PORTALS = "noreply@local-portals"
NOW = datetime.now(timezone.utc)
random.seed(17)


def day(days_ago: int, hhmm: str) -> datetime:
    """A time ``days_ago`` days back, at that local (India) time."""
    hours, minutes = map(int, hhmm.split(":"))
    local = (datetime.now().astimezone() - timedelta(days=days_ago)).replace(
        hour=hours, minute=minutes, second=random.randint(3, 57), microsecond=0
    )
    return local.astimezone(timezone.utc)


def ago(hours: float) -> datetime:
    return (NOW - timedelta(hours=hours)).replace(microsecond=0)


# The jobs ---------------------------------------------------------------------------


@dataclass
class Job:
    key: str
    story: str
    mbl: str
    hbls: list[str]
    shipper: str
    vessel: str
    voyage: str
    pol: str
    goods: str
    owner: str | None
    sizes: list[str] = field(default_factory=lambda: ["40HC"])  # the container types, one per container
    containers: list[str] = field(default_factory=list)
    id: str = ""
    job_no: str = ""


def _box(prefix: str) -> str:
    return containers.with_check_digit(prefix, f"{random.randint(100000, 999999)}")


JOBS = [
    Job("A", "eDO received: every step done", "SHZ8462190", ["WOLSZSE26082211A"],
        "SHENZHEN HUAXIN GARMENT CO., LTD", "APL SAVANNAH", "0MDB3W1MA", "YANTIAN, CHINA",
        "LADIES KNITTED TOPS", "Ravi Sharma"),
    Job("B", "DO requested: waiting for CMA CGM's eDO", "GGZ3215560", ["MSEC260900052318"],
        "GUANGZHOU MEILIN TEXTILE CO., LTD", "CMA CGM TAGE", "0FE5LW1MA", "NANSHA, CHINA",
        "WOMEN'S WOVEN DRESSES", "Sneha Nair", sizes=["20GP"]),
    Job("C", "DO folder built: the DO request waits for ops' approval", "SHZ8479913",
        ["WOLSZSE26085090A", "WOLSZSE26085090B"], "DONGGUAN YUEXING APPAREL CO., LTD", "XIN MEI ZHOU",
        "0MDJ2W1MA", "SHEKOU, CHINA", "MEN'S COTTON SHIRTS", "Arjun Verma", sizes=["40HC", "40HC"]),
    Job("D", "Deposit approved, but the DO Receipt attached is another job's", "GGZ3229047", ["MSEC260900060775"],
        "GUANGZHOU HONGDA KNITTING CO., LTD", "APL CHANGI", "0FBK3W1MA", "NANSHA, CHINA",
        "KNITTED SWEATERS", "Meera Iyer"),
    Job("E", "Deposit raised on ODeX, waiting for CMA CGM's approval; Finance's reply read by the AI",
        "SHZ8495502", ["WOLSZSE26090314A"], "SHENZHEN RUIXIANG FASHION CO., LTD", "CMA CGM BALI", "0FE7NW1MA",
        "YANTIAN, CHINA", "LADIES WOVEN TROUSERS", "Kunal Mehta", sizes=["20GP"]),
    Job("F", "Waiting for Finance's UTRs: Finance replied with a wrong MBL", "SHZ8501274", ["WOLSZSE26091102A"],
        "SHENZHEN JIAHE APPAREL CO., LTD", "CMA CGM RIGOLETTO", "0FBQ7W1MA", "SHEKOU, CHINA",
        "GIRLS' COTTON DRESSES", "Priya Desai"),
    Job("K", "Invoice requested: CMA CGM hasn't released it for over a day", "GGZ3233816", ["MSEC261000008841"],
        "FOSHAN LIANGYI GARMENT CO., LTD", "APL PARIS", "0MDL5W1MA", "NANSHA, CHINA", "MEN'S DENIM JEANS",
        "Rohit Bansal", sizes=["40GP"]),
    Job("G", "The browser agent's invoice request failed: ODeX didn't answer", "GGZ3240176", ["MSEC261000013309"],
        "ZHONGSHAN YIFAN TEXTILE CO., LTD", "CMA CGM VELA", "0FE9TW1MA", "NANSHA, CHINA",
        "LADIES COTTON KURTAS", None),
    Job("H", "The pre-alert's HBL is a scan: the app couldn't read the house bill", "SHZ8519308",
        ["WOLSZSE26101407A"], "SHENZHEN BAILU GARMENT CO., LTD", "CMA CGM ARGENTINA", "0FBT1W1MA",
        "YANTIAN, CHINA", "MEN'S POLO T-SHIRTS", None),
]
# Older jobs, every step done: the finance page's invoices then span more than one month.
JOBS += [
    Job("L", "eDO received in September", "SHZ8391046", ["WOLSZSE26090219A"], "SHENZHEN HUAXIN GARMENT CO., LTD",
        "CMA CGM TAGE", "0FE1LW1MA", "YANTIAN, CHINA", "LADIES KNITTED TOPS", "Ravi Sharma", sizes=["40HC", "20GP"]),
    Job("M", "eDO received in September", "GGZ3171255", ["MSEC260800071542"], "GUANGZHOU MEILIN TEXTILE CO., LTD",
        "APL SAVANNAH", "0MDA7W1MA", "NANSHA, CHINA", "WOMEN'S WOVEN DRESSES", "Sneha Nair", sizes=["20GP"]),
    Job("N", "eDO received in September", "SHZ8420377", ["WOLSZSE26091644A", "WOLSZSE26091644B"],
        "DONGGUAN YUEXING APPAREL CO., LTD", "XIN MEI ZHOU", "0MDG4W1MA", "SHEKOU, CHINA", "MEN'S COTTON SHIRTS",
        "Arjun Verma", sizes=["40HC", "40HC"]),
    Job("P", "eDO received this month", "GGZ3190628", ["MSEC260900023105"], "FOSHAN LIANGYI GARMENT CO., LTD",
        "CMA CGM BALI", "0FE3NW1MA", "NANSHA, CHINA", "MEN'S DENIM JEANS", "Meera Iyer", sizes=["40GP"]),
]
# A pre-alert someone forwarded without #DOHACK: no job opened.
UNMARKED = Job("I", "Pre-alert forwarded without #DOHACK: no job", "SHZ8521746", ["WOLSZSE26101512A"],
               "SHENZHEN QINGYUN APPAREL CO., LTD", "APL CHARLESTON", "0MDN8W1MA", "YANTIAN, CHINA",
               "WOMEN'S KNITTED CARDIGANS", None)


# Documents --------------------------------------------------------------------------

HEXALOG = ["HEXALOG GLOBAL SUPPLY CHAINS PVT LTD", "UNIT 1204, TOWER B, GURUGRAM, HARYANA 122002, INDIA",
           "GSTIN: 06AAACH0000X1ZQ"]
ENVOGUE = ["ENVOGUE STYLES GLOBAL LLP", "OKHLA INDUSTRIAL AREA PHASE II, NEW DELHI 110020, INDIA"]


def bill_pdf(job: Job, *, house: str | None, issued: datetime) -> bytes:
    """A B/L as CMA CGM (the MBL) or the origin forwarder (an HBL) prints it, with the
    labels app/email_ingest.py reads: the consignee and "Ocean vessel. NAME V.VOYAGE"."""
    out = io.BytesIO()
    page = canvas.Canvas(out, pagesize=A4)
    width, height = A4
    left, right = 14 * mm, width - 14 * mm
    middle = width / 2
    page.setTitle(f"{'HBL ' + house if house else 'MBL ' + job.mbl}")

    if house:
        page.setFont("Helvetica-Bold", 14)
        page.drawString(left, height - 20 * mm, "WOL SHENZHEN INTERNATIONAL LOGISTICS CO., LTD"
                        if house.startswith("WOL") else "MSEC FREIGHT (GUANGZHOU) LIMITED")
        page.setFont("Helvetica", 8)
        page.drawString(left, height - 24.5 * mm, "NVOCC  ·  International freight forwarder")
        title, number = "HOUSE BILL OF LADING", house
    else:
        page.setFont("Helvetica-Bold", 18)
        page.setFillColor(Color(0.0, 0.16, 0.45))
        page.drawString(left, height - 20 * mm, "CMA CGM")
        page.setFillColor(Color(0, 0, 0))
        page.setFont("Helvetica", 8)
        page.drawString(left, height - 24.5 * mm, "CMA CGM S.A.  ·  4 Quai d'Arenc, 13002 Marseille, France")
        title, number = "SEA WAYBILL (DRAFT)", job.mbl
    page.setFont("Helvetica-Bold", 12)
    page.drawRightString(right, height - 19 * mm, title)
    page.setFont("Helvetica-Bold", 10)
    page.drawRightString(right, height - 25 * mm, f"B/L No. {number}")

    def box(x: float, top: float, w: float, h: float, label: str, lines: list[str]) -> None:
        page.setStrokeColor(Color(0.55, 0.55, 0.55))
        page.rect(x, top - h, w, h)
        page.setFont("Helvetica", 6.5)
        page.setFillColor(Color(0.35, 0.35, 0.35))
        page.drawString(x + 2 * mm, top - 3.5 * mm, label)
        page.setFillColor(Color(0, 0, 0))
        page.setFont("Helvetica", 8.5)
        for row, line in enumerate(lines):
            page.drawString(x + 2 * mm, top - 8 * mm - row * 4 * mm, line)

    top = height - 30 * mm
    consignee = ENVOGUE if house else HEXALOG
    shipper = [job.shipper, "BAO'AN DISTRICT, SHENZHEN, GUANGDONG, CHINA"] if house else [
        "WOL SHENZHEN INTERNATIONAL LOGISTICS CO., LTD" if job.hbls[0].startswith("WOL")
        else "MSEC FREIGHT (GUANGZHOU) LIMITED", "ON BEHALF OF " + job.shipper]
    box(left, top, middle - left, 26 * mm, "Shipper", shipper)
    references = [f"Booking ref.: {job.mbl[:3]}{job.mbl[-6:]}B", f"Export ref.: SZX-EXP-26{job.mbl[-5:]}"]
    if house:
        references.append(f"Master B/L: CMA CGM {job.mbl}")
    box(middle, top, right - middle, 26 * mm, "Booking and export references", references)
    top -= 26 * mm
    box(left, top, middle - left, 26 * mm, "Consignee (Complete name and address)", consignee)
    box(middle, top, right - middle, 26 * mm, "Notify party", ["SAME AS CONSIGNEE"] if house else ENVOGUE[:1] + [
        "C/O " + HEXALOG[0]])
    top -= 26 * mm
    # One text run, as on CMA CGM's draft: "Ocean vessel. NAME V.VOYAGE".
    page.rect(left, top - 12 * mm, middle - left, 12 * mm)
    page.setFont("Helvetica", 8.5)
    page.drawString(left + 2 * mm, top - 7 * mm, f"Ocean vessel. {job.vessel} V.{job.voyage}")
    box(middle, top, right - middle, 12 * mm, "Port of loading", [job.pol])
    top -= 12 * mm
    box(left, top, middle - left, 12 * mm, "Port of discharge", ["NHAVA SHEVA, INDIA"])
    box(middle, top, right - middle, 12 * mm, "Place of delivery", ["ICD TUGHLAKABAD (NEW DELHI), INDIA"])
    top -= 16 * mm

    page.setFont("Helvetica-Bold", 7.5)
    for x, label in ((left, "Container no. / Seal no."), (left + 52 * mm, "Packages"),
                     (left + 78 * mm, "Description of goods"), (right - 42 * mm, "Gross weight"),
                     (right - 18 * mm, "Measure")):
        page.drawString(x, top, label)
    page.line(left, top - 2 * mm, right, top - 2 * mm)
    page.setFont("Helvetica", 8.5)
    row_top = top - 7 * mm
    for box_no in job.containers:
        cartons = random.randint(260, 640)
        page.drawString(left, row_top, f"{box_no} / CN{random.randint(1000000, 9999999)}")
        page.drawString(left + 52 * mm, row_top, f"{cartons} CTNS")
        page.drawString(left + 78 * mm, row_top, job.goods)
        page.drawRightString(right - 22 * mm, row_top, f"{cartons * 14.5:,.3f} KGS")
        page.drawRightString(right, row_top, f"{cartons * 0.12:,.3f} CBM")
        row_top -= 5 * mm
    page.drawString(left + 78 * mm, row_top - 2 * mm, "HS CODE 6104 / 6204 / 6205")
    page.drawString(left + 78 * mm, row_top - 6 * mm, "SHIPPER'S LOAD, STOW AND COUNT")
    page.drawString(left + 78 * mm, row_top - 10 * mm, "FREIGHT PREPAID")

    page.line(left, 52 * mm, right, 52 * mm)
    page.setFont("Helvetica", 8)
    page.drawString(left, 46 * mm, f"Place and date of issue: {job.pol.split(',')[0]}, {issued:%d-%b-%Y}".upper())
    page.drawString(left, 41 * mm, "Number of original B/Ls: ZERO (0)" if not house else "Number of original B/Ls: THREE (3)")
    page.drawRightString(right, 41 * mm, "AS AGENT FOR THE CARRIER CMA CGM S.A." if not house else "AS CARRIER")
    page.setFont("Helvetica-Oblique", 7)
    page.drawString(left, 30 * mm, "Received by the carrier in apparent good order and condition unless otherwise "
                    "stated, the goods or packages specified herein,")
    page.drawString(left, 26 * mm, "to be transported to the place of delivery subject to the terms and conditions "
                    "of this bill of lading.")
    if not house:
        page.saveState()
        page.setFillColor(Color(0.8, 0.1, 0.1, alpha=0.12))
        page.setFont("Helvetica-Bold", 96)
        page.translate(width / 2, height / 2)
        page.rotate(32)
        page.drawCentredString(0, 0, "DRAFT")
        page.restoreState()
    page.showPage()
    page.save()
    return out.getvalue()


def scanned_bill(job: Job, house: str) -> bytes:
    """A house bill as a phone scan: a picture with no text in it."""
    sheet = Image.new("L", (1240, 1754), 246)
    draw = ImageDraw.Draw(sheet)

    def font(size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)

    draw.text((80, 70), "WOL SHENZHEN INTERNATIONAL LOGISTICS CO., LTD", fill=40, font=font(30))
    draw.text((780, 130), "HOUSE BILL OF LADING", fill=40, font=font(28))
    draw.text((780, 175), f"B/L No. {house}", fill=40, font=font(26))
    y = 240
    for label, lines in (
        ("Shipper", [job.shipper, "BAO'AN DISTRICT, SHENZHEN, CHINA"]),
        ("Consignee (Complete name and address)", ENVOGUE),
        ("Notify party", ["SAME AS CONSIGNEE"]),
        ("Ocean vessel / Port of loading", [f"{job.vessel} V.{job.voyage}   {job.pol}"]),
        ("Port of discharge / Place of delivery", ["NHAVA SHEVA / ICD TUGHLAKABAD (NEW DELHI)"]),
    ):
        draw.rectangle((70, y, 1170, y + 170), outline=110, width=2)
        draw.text((85, y + 12), label, fill=90, font=font(20))
        for row, line in enumerate(lines):
            draw.text((85, y + 55 + row * 38), line, fill=35, font=font(26))
        y += 190
    draw.text((85, y + 20), f"{job.containers[0]}    412 CTNS    {job.goods}", fill=35, font=font(26))
    noisy = sheet.rotate(0.7, fillcolor=228).filter(ImageFilter.GaussianBlur(0.8))
    out = io.BytesIO()
    noisy.convert("RGB").save(out, "PDF", resolution=150)
    return out.getvalue()


# The app, in this process -----------------------------------------------------------


class Recorder(mocked.Inngest):
    """Keeps the events the app sends. Each is followed by hand below, in its own time."""

    def __init__(self) -> None:
        super().__init__(app_id="do-workflow-demo-data", is_production=False)
        self.sent: list[inngest.Event] = []

    def send_sync(self, events, *, skip_middleware: bool = False) -> list[str]:  # noqa: ANN001
        batch = events if isinstance(events, list) else [events]
        self.sent.extend(batch)
        return ["demo"] * len(batch)

    async def send(self, events, *, skip_middleware: bool = False) -> list[str]:  # noqa: ANN001
        return self.send_sync(events)


STAMPED = {
    Shipment: ("created_at", "updated_at", "changed_at", "folder_built_at"),
    Document: ("uploaded_at",),
    Event: ("at",),
    Task: ("created_at", "updated_at", "status_since", "completed_at"),
    AutomationRun: ("started_at", "completed_at"),
    InboundMail: ("at",),
    IdempotencyKey: ("created_at", "updated_at"),
}
SOPS = {  # what the browser agent's run for each step is called, its steps, and about how long it takes
    portal_params.REQUEST_INVOICE: ("Local ODeX - Request New Invoice", 28, 46),
    portal_params.INVOICE_FETCH: ("Local ODeX - Collect the DO invoice", 23, 38),
    portal_params.RTGS_SHIPMENT: ("CMA CGM RTGS - New Request", 26, 52),
    portal_params.RTGS_DEPOSIT: ("CMA CGM RTGS - New Request", 26, 49),
    portal_params.DEPOSIT: ("Local ODeX - Security Deposit", 50, 84),
    portal_params.DO_REQUEST_STATUS: ("Local ODeX - DO request status and eDO", 24, 33),
}


class Demo:
    def __init__(self) -> None:
        base = Settings.from_env()
        self.settings = replace(
            base, automate_portals=True, browser_headless=True, seed_demo_data=False,
            odex_sop_path=BACKEND_DIR / "sops" / "local" / "odex" / "do_request_new.yaml",
        )
        self.client = Recorder()
        self.app = create_app(self.settings, self.client)
        self.app.dependency_overrides[current_user] = lambda: "demo data script"  # the API asks for a sign-in
        self.rt = self.app.state.rt
        self.api = TestClient(self.app)
        self.functions = self.app.state.functions
        self.store = portal_store.Store(portal_data_dir())
        with self.rt.sessionmaker() as session:
            profile = service.load_profile(session).data
        self.finance = profile.get("finance_email")
        if not self.finance:
            raise SystemExit("Fill Finance's email in the fixed details first: the jobs' notes name it.")
        self.payer_account = profile.get("payer_account_no") or "123456789012"

    # Time ---------------------------------------------------------------------------

    @contextmanager
    def at(self, when: datetime):
        """Whatever the app writes inside the block is dated ``when``."""
        mark = datetime.now(timezone.utc)
        yield
        with self.rt.sessionmaker() as session:
            for model, columns in STAMPED.items():
                for column in columns:
                    attribute = getattr(model, column)
                    session.execute(update(model).where(attribute >= mark).values({column: when}))
            session.commit()

    @contextmanager
    def portal_clock(self, when: datetime):
        """The local portals' records, numbers and documents dated ``when``."""
        local = when.astimezone().replace(tzinfo=None)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ANN001, ANN206
                return local

        class Today(date):
            @classmethod
            def today(cls):  # noqa: ANN206
                return local.date()

        saved = portal_store.datetime, cma.date
        portal_store.datetime, cma.date = Clock, Today
        try:
            yield local.isoformat(timespec="seconds")
        finally:
            portal_store.datetime, cma.date = saved

    # The app's functions and API ---------------------------------------------------

    def run(self, fn_id: str, name: str, data: dict[str, Any]) -> Any:
        fn = self.functions[fn_id]
        event = inngest.Event(name=name, data=data)
        result = mocked.trigger(fn, event, self.client)
        if result.status is mocked.Status.FAILED and fn._opts.on_failure is not None:  # noqa: SLF001
            failed = inngest.Event(
                name="inngest/function.failed",
                data={"error": {"message": str(result.error)}, "event": event.model_dump(), "function_id": fn.id},
            )
            asyncio.run(fn._opts.on_failure(SimpleNamespace(event=failed)))  # noqa: SLF001
        return result

    def mail(self, when: datetime, subject: str, body: str, files: list[tuple[str, bytes]], sender: str) -> str:
        """A mail arrives in the app's mailbox and the mail workflow reads it."""
        message_id = uuid.uuid4().hex
        folder = self.settings.storage_dir / "inbox" / message_id
        (folder / "attachments").mkdir(parents=True)
        for name, data in files:
            (folder / "attachments" / name).write_bytes(data)
        message = {
            "id": message_id, "subject": subject, "sender": {"emailAddress": {"address": sender}},
            "body": {"contentType": "text", "content": body}, "hasAttachments": bool(files),
            "receivedDateTime": when.isoformat(),
        }
        (folder / "message.json").write_text(json.dumps(message, indent=2))
        self.client.sent.clear()
        with self.at(when):
            result = self.run("email-inbound", EventNames.EMAIL_INBOUND, {"message_id": message_id})
            if result.status is not mocked.Status.COMPLETED:
                raise SystemExit(f"The mail workflow failed on {subject!r}: {result.error}")
            for sid in {e.data.get("shipment_id") for e in self.client.sent if e.data.get("shipment_id")}:
                service.refresh_folder_review_job(self.rt, sid)
        return message_id

    def delivered(self, when: datetime) -> None:
        """The local CMA CGM's new mails reach the app, as mail.deliver_pending hands them over."""
        with self.store.read() as data:
            pending = [dict(m) for m in data["outbox"] if not m.get("delivered")]
        for item in reversed(pending):
            codes = [f"{self.settings.inject_code_prefix}:{':'.join(p for p in code if p)}" for code in item.get("codes", [])]
            files = []
            for found in (self.store.file(f) for f in item["attachments"]):
                if found is not None:
                    files.append((found[0], found[1].read_bytes()))
            self.mail(when, f"{self.settings.mail_marker} {item['subject']}", "\n".join([item["body"], *codes]), files, PORTALS)
            with self.store.change() as data:
                for mail in data["outbox"]:
                    if mail["id"] == item["id"]:
                        mail["delivered"] = when.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")

    def dropped(self, when: datetime) -> None:
        """CMA CGM's mail that went to the team's mailbox and was never forwarded to the app."""
        with self.store.change() as data:
            for mail in data["outbox"]:
                mail.setdefault("delivered", when.astimezone().replace(tzinfo=None).isoformat(timespec="seconds"))

    def ops_approve(self, when: datetime, job: Job, task_key: str) -> None:
        with self.at(when):
            reply = self.api.post(f"/api/shipments/{job.id}/tasks/{task_key}/complete", json={"by": job.owner or "ops"})
            if reply.status_code != 202:
                raise SystemExit(f"Approving {task_key} on {job.job_no} failed: {reply.text}")
        self.client.sent.clear()

    def agent_done(self, when: datetime, job: Job, step_key: str, readings: dict[str, dict[str, Any]]) -> None:
        """The browser agent ran the step on the portal and read these back."""
        task_key = process.TASK_OF[step_key]
        sop, steps, seconds = SOPS[step_key]
        with self.at(when):
            process.start_job(self.rt, job.id, task_key, retry=False)
            process.record_job(self.rt, job.id, step_key, readings)
            with self.rt.sessionmaker() as session:
                for _ in readings:
                    session.add(AutomationRun(
                        run_id=str(uuid.uuid4()), shipment_id=job.id, task_key=task_key, sop_name=sop,
                        status="completed", total_steps=steps, completed_steps=steps,
                        duration_seconds=seconds + random.randint(-6, 9), completed_at=datetime.now(timezone.utc),
                    ))
                session.commit()
        with self.rt.sessionmaker() as session:
            for run in session.scalars(select(AutomationRun).where(
                AutomationRun.shipment_id == job.id, AutomationRun.started_at == when, AutomationRun.task_key == task_key,
            )):
                run.started_at = when - timedelta(seconds=run.duration_seconds or 40)
            session.commit()

    def shipment(self, job: Job) -> Shipment:
        with self.rt.sessionmaker() as session:
            return service.load_shipment(session, job.id)


# Each step of the process, as it happens for real ------------------------------------


def pre_alert(demo: Demo, job: Job, when: datetime, *, marker: bool = True, scanned: bool = False) -> None:
    """The forwarder's pre-alert, forwarded by the team: the draft MBL and the HBLs."""
    files = [("MBL DRAFT.pdf", bill_pdf(job, house=None, issued=when - timedelta(days=9)))]
    for hbl in job.hbls:
        name = f"{hbl[-9:]} HBL.pdf"
        files.append((name, scanned_bill(job, hbl) if scanned else bill_pdf(job, house=hbl, issued=when - timedelta(days=9))))
    hbls = " / ".join(job.hbls)
    subject = f"FW: PRE ALERT // CMA CGM // MBL {job.mbl} // HBL {hbls} // ETA NHAVA SHEVA {(when + timedelta(days=9)):%d-%b}".upper()
    body = (
        "Dear Team,\n\nPlease find attached the draft MBL and HBL for the below shipment.\n\n"
        f"MBL: {job.mbl}\nHBL: {hbls}\nVessel: {job.vessel} V.{job.voyage}\nPOL: {job.pol}\n"
        "POD: NHAVA SHEVA / ICD TUGHLAKABAD\n"
        f"Containers: {', '.join(job.containers)}\nCommodity: {job.goods}\n\n"
        "Kindly arrange the DO in advance.\n\nBest regards,\nExport Documentation\n"
    )
    demo.mail(when, f"{'#DOHACK ' if marker else ''}{subject}", body, files, OPS)
    if not marker:
        return
    with demo.rt.sessionmaker() as session:
        job.id = next(s.id for s in session.scalars(select(Shipment)) if s.data.get("mbl_no") == job.mbl)


def renumber(demo: Demo, job: Job, opened: datetime) -> None:
    """The job number in the order the jobs opened, its owner, and the letters dated that day."""
    with demo.rt.sessionmaker() as session:
        shipment = service.load_shipment(session, job.id)
        old = shipment.job_no
        shipment.job_no = job.job_no
        shipment.owner = job.owner
        shipment.data = {**shipment.data, "letter_date": opened.astimezone().date().isoformat()}
        for event in session.scalars(select(Event).where(Event.shipment_id == job.id)):
            event.detail = event.detail.replace(old, job.job_no)
        session.commit()


def request_invoice(demo: Demo, job: Job, when: datetime) -> dict[str, Any]:
    """The browser agent requests the DO invoice on ODeX (the request sits on the local ODeX)."""
    with demo.portal_clock(when) as stamp, demo.store.change() as data:
        record = {
            "id": secrets.token_hex(6), "ref": portal_store.next_ref(data, "BL", 5),
            "liner": "CMA CGM AGENCIES INDIA PRIVATE LIMITED", "bl_no": job.mbl, "bl_location": "NCR",
            "type": "Import", "delivery_type": "Factory Destuff", "job_no": job.job_no, "containers": job.containers,
            "sizes": job.sizes, "vessel": job.vessel, "voyage": job.voyage,
            "attachments": [], "status": "Pending", "created_at": stamp, "by": "demo-cha@local-odex",
        }
        data["invoice_requests"].append(record)
    demo.agent_done(when, job, portal_params.REQUEST_INVOICE, {job.mbl: {"odex_invoice_request_ref": record["ref"]}})
    return record


def release_invoice(demo: Demo, job: Job, request: dict[str, Any], when: datetime) -> tuple[str, float]:
    """CMA CGM releases the DO invoice; ODeX's notice reaches the app; the agent collects it."""
    invoice_no = f"INIUPC2703{random.randint(1000, 9999)}"
    usd_rate = round(random.uniform(99.62, 100.48), 6)  # the dollar moves from week to week
    charges, taxes = cma.invoice_lines(job.sizes, usd_rate)
    total = round(sum(c["amount_inr"] for c in charges) + sum(t["amount"] for t in taxes), 2)
    with demo.portal_clock(when) as stamp, demo.store.change() as data:
        pdf = portal_docs.invoice_pdf(
            invoice_no=invoice_no, on=stamp[:10], bl_no=job.mbl, vessel=job.vessel, voyage=job.voyage,
            containers=job.containers, charges=charges, taxes=taxes, usd_rate=usd_rate, request_ref=request["ref"],
        )
        file_id = demo.store.add_file(data, pdf, f"{invoice_no}.pdf")
        for item in data["invoice_requests"]:
            if item["id"] == request["id"]:
                item.update(status="Released", invoice_no=invoice_no, amount=total, invoice_file=file_id,
                            invoice_date=stamp[:10], answered_at=stamp)
    demo.mail(
        when, f"#DOHACK ODeX: Invoices Received for {job.mbl}",
        f"Dear Sir/Madam,\n\nInvoices released for {job.mbl},\nType Of Delivery:   Factory Destuff\n"
        "IGM No.:\nIGM Date:\nCFS Name:\n\nInvoice No.\tInvoice Type\tTotal Invoice Amt.\tBill to Party\n"
        f"{invoice_no}\tNON FREIGHT\t{total:.2f}\tHEXALOG GLOBAL SUPPLY CHAINS PVT LTD\n\n"
        "Note:This is a system generated e-mail. Please do NOT reply to it.\n\n"
        "Above notification on behalf of CMA CGM AGENCIES INDIA PRIVATE LIMITED\n\nRegards,\nODeX Support Team\n",
        [], OPS,
    )
    collected = when + timedelta(minutes=2)
    path = Path(portal_params.invoice_pdf_path(demo.rt, demo.shipment(job)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    demo.agent_done(collected, job, portal_params.INVOICE_FETCH,
                    {job.mbl: {"invoice_no": invoice_no, "invoice_amount": f"{total:.2f}", "invoice_status": "Released"}})
    return invoice_no, total


def mail_finance(demo: Demo, job: Job, when: datetime) -> None:
    with demo.rt.sessionmaker() as session:
        cc = process._addresses(service.load_profile(session).data.get("mail_cc"))  # noqa: SLF001
    with demo.at(when):
        process.record_finance_mail_job(demo.rt, job.id, {"to": [demo.finance], "cc": cc, "id": uuid.uuid4().hex})


def finance_replies(demo: Demo, job: Job, when: datetime, payment: str, deposit: str, *, body: str | None = None,
                    mbl_in_body: str | None = None) -> None:
    subject = f"RE: {job.job_no} // {job.mbl} // CMA // #DOHACK"
    quoted = (
        f"\n\nFrom: Customs Operations\nSubject: {job.job_no} // {job.mbl} // CMA //\n\nDear Finance team,\n"
        "PFA Shipping line invoice\nKindly arrange payment & share UTR Details\n"
        "Also please arrange Security Amount of Rs. 40,000/-\nPlease share the UTRs on these lines:\n"
        "Invoice UTR: \nDeposit UTR: \n"
    )
    if body is None:
        named = f" for MBL {mbl_in_body}" if mbl_in_body else ""
        body = f"Hi team,\n\nPayment done{named}.\n\nInvoice UTR: {payment}\nDeposit UTR: {deposit}\n\nRegards,\nAccounts"
    demo.mail(when, subject, body + quoted, [], demo.finance)
    with demo.rt.sessionmaker() as session:
        shipment = service.load_shipment(session, job.id)
        if shipment.data.get("payment_utr"):  # fields_for() dates the transfer the day the UTR comes in: today
            shipment.data = {**shipment.data, "payment_date": when.astimezone().date().isoformat()}
            session.commit()
    with demo.at(when):
        process.refresh_payments_job(demo.rt, job.id)


def rtgs(demo: Demo, job: Job, when: datetime, *, deposit: bool, invoice_no: str, amount: float, utr: str,
         paid_on: date) -> dict[str, Any]:
    """Ops approve; the agent confirms the payment on CMA CGM's RTGS page."""
    task_key = tasks.CMA_RTGS_DEPOSIT if deposit else tasks.CMA_RTGS_PAYMENT
    demo.ops_approve(when, job, task_key)
    done = when + timedelta(minutes=2)
    with demo.portal_clock(done) as stamp, demo.store.change() as data:
        record = {
            "id": secrets.token_hex(6), "ref": str(91500000 + portal_store.next_number(data, "rtgs_reference")),
            "carrier": "CMA CGM", "company_name": "HEXALOG GLOBAL SUPPLY CHAINS PVT LTD",
            "contact_name": "Kishan Rawat", "contact_number": "8802757004",
            "emails": ["customs-ops@Hexalog.in"], "utr": utr, "currency": "INR", "bank_account": demo.payer_account,
            "amount": 40000.0 if deposit else amount, "transfer_date": paid_on.isoformat(),
            "invoices": [{"security_deposit": deposit, "invoice_no": "" if deposit else invoice_no, "bl_no": job.mbl,
                          "amount": 40000.0 if deposit else amount, "remarks": ""}],
            "advance_amount": None, "bl_no": job.mbl, "status": "Received", "created_at": stamp,
        }
        data["rtgs_requests"].append(record)
    step = portal_params.RTGS_DEPOSIT if deposit else portal_params.RTGS_SHIPMENT
    demo.agent_done(done, job, step, {job.mbl: {"cma_rtgs_request_ref": record["ref"]}})
    return record


def receipt(demo: Demo, job: Job, record: dict[str, Any], when: datetime, *, forwarded: bool = True) -> None:
    """CMA CGM confirms the payment and mails its receipt; the team forwards it to the app."""
    with demo.portal_clock(when):
        cma.send_receipts(demo.store, record["id"])
    if forwarded:
        demo.delivered(when)
    else:
        demo.dropped(when)
    with demo.at(when):
        process.refresh_payments_job(demo.rt, job.id)


def raise_deposit(demo: Demo, job: Job, when: datetime, utr: str, paid_on: date) -> list[dict[str, Any]]:
    """Ops approve; the agent raises the security deposit on ODeX for each house bill."""
    demo.ops_approve(when, job, tasks.ODEX_DEPOSIT)
    done = when + timedelta(minutes=3)
    shipment = demo.shipment(job)
    records = []
    with demo.portal_clock(done) as stamp, demo.store.change() as data:
        for hbl in job.hbls:
            record = {
                "id": secrets.token_hex(6), "ref": portal_store.next_ref(data, "SD", 5),
                "line": "CMA CGM AGENCIES INDIA PRIVATE LIMITED", "bl_no": hbl, "mbl_no": job.mbl, "location": "NCR",
                "fpod": "ICD TUGHLAKABAD", "consignee": ENVOGUE[0], "mode_of_payment": "RTGS/NEFT",
                "refund_to": "Third Party", "refund_to_name": "JAY SHRI RAMDARBAR LOGISTICS PRIVATE LIMITED",
                "refund_pan": "", "customer_remarks": "", "attachments": [], "bank_name": "ICICI BANK",
                "amount": 40000.0, "utr": utr, "payment_date": paid_on.isoformat(),
                "ireceipt_no": shipment.data.get("security_receipt_no") or "", "refund_account": {},
                "status": "Deposit Requested", "created_at": stamp, "by": "demo-cha@local-odex",
            }
            data["deposits"].append(record)
            records.append(record)
    demo.agent_done(done, job, portal_params.DEPOSIT, {r["bl_no"]: {"odex_deposit_ref": r["ref"]} for r in records})
    return records


def approve_deposit(demo: Demo, job: Job, records: list[dict[str, Any]], when: datetime) -> None:
    """CMA CGM approves the deposit; ODeX mails the slip for each house bill."""
    with demo.portal_clock(when):
        for record in records:
            cma.approve_deposit(demo.store, record["id"])
    demo.delivered(when)
    with demo.at(when):
        process.refresh_payments_job(demo.rt, job.id)
        service.refresh_folder_review_job(demo.rt, job.id)


def build(demo: Demo, job: Job, when: datetime) -> None:
    """Ops review the folder and build it; the DO request goes in front of ops."""
    with demo.at(when):
        reply = demo.api.post(f"/api/shipments/{job.id}/build", json={"confirmed": True, "by": job.owner})
        if reply.status_code != 202:
            raise SystemExit(f"Building {job.job_no} failed: {reply.text}")
        service.build_folder_job(demo.rt, job.id)
        service.open_do_request_job(demo.rt, job.id)
    demo.client.sent.clear()


def request_do(demo: Demo, job: Job, when: datetime) -> list[dict[str, Any]]:
    """Ops approve; the agent raises the DO request on ODeX for each house bill."""
    demo.ops_approve(when, job, tasks.ODEX_DO_REQUEST)
    done = when + timedelta(minutes=4)
    records = []
    with demo.portal_clock(done) as stamp, demo.store.change() as data:
        for hbl in job.hbls:
            record = {
                "id": secrets.token_hex(6), "ref": portal_store.next_ref(data, "DOR", 4), "bl_no": hbl,
                "liner": "CMA CGM AGENCIES INDIA PRIVATE LIMITED", "location": "NCR", "cargo_type": "FCL",
                "destuff_type": "Factory Destuff", "seaway_bl": "Yes", "payment_not_via_odex": True,
                "runner_boy": "", "mobile_no": "", "remarks": "Forwarder Letter attached to authorise CHA",
                "hss_customer_name": HEXALOG[0], "factory_address": "", "factory_pin": "122015",
                "containers": job.containers, "documents": [], "status": "Submitted", "created_at": stamp,
                "by": "demo-cha@local-odex",
            }
            data["do_requests"].append(record)
            records.append(record)
    with demo.at(done):
        service.record_do_request_job(demo.rt, job.id, {r["bl_no"]: {"reference": r["ref"]} for r in records}, by="browser agent")
        with demo.rt.sessionmaker() as session:
            session.add(AutomationRun(
                run_id=str(uuid.uuid4()), shipment_id=job.id, task_key=tasks.ODEX_DO_REQUEST,
                sop_name="Local ODeX - New DO Request", status="completed", total_steps=39, completed_steps=39,
                duration_seconds=71, completed_at=datetime.now(timezone.utc),
            ))
            shipment = service.load_shipment(session, job.id)
            shipment.requested_on = done.astimezone().date()
            session.commit()
    return records


def issue_edo(demo: Demo, job: Job, records: list[dict[str, Any]], when: datetime) -> None:
    """CMA CGM issues the eDO; its mail reaches the app; the agent collects it from ODeX."""
    with demo.portal_clock(when):
        for record in records:
            cma.issue_edo(demo.store, record["id"])
    demo.delivered(when)
    collected = when + timedelta(minutes=3)
    readings = {}
    shipment = demo.shipment(job)
    with demo.store.read() as data:
        files = {r["bl_no"]: r.get("edo_file") for r in data["do_requests"] if r["id"] in {x["id"] for x in records}}
    for hbl in job.hbls:
        found = demo.store.file(files.get(hbl) or "")
        target = Path(portal_params.invoice_pdf_path(demo.rt, shipment)).parent / f"{hbl}-eDO.pdf"
        if found is not None:
            target.write_bytes(found[1].read_bytes())
        readings[hbl] = {"downloaded_file_path": str(target)}
    demo.agent_done(collected, job, portal_params.DO_REQUEST_STATUS, readings)
    with demo.rt.sessionmaker() as session:
        service.load_shipment(session, job.id).edo_received_on = collected.astimezone().date()
        session.commit()


def utr() -> str:
    return f"6289{random.randint(10000000, 99999999)}"


def paid_through_receipts(demo: Demo, job: Job, opened: datetime, *, receipt_forwarded: bool = True,
                          finance_body: str | None = None) -> dict[str, Any]:
    """From the pre-alert to both receipts in, at the pace the team works."""
    request = request_invoice(demo, job, opened + timedelta(minutes=2))
    released = opened + timedelta(days=1, hours=4, minutes=11)
    invoice_no, amount = release_invoice(demo, job, request, released)
    mail_finance(demo, job, released + timedelta(minutes=3))
    replied = opened + timedelta(days=2, hours=1, minutes=12)
    payment, deposit = utr(), utr()
    finance_replies(demo, job, replied, payment, deposit, body=finance_body)
    shipment = demo.shipment(job)
    payment, deposit = shipment.data.get("payment_utr") or payment, shipment.data.get("deposit_utr") or deposit
    paid_on = replied.astimezone().date()
    approved = replied + timedelta(minutes=41)
    first = rtgs(demo, job, approved, deposit=False, invoice_no=invoice_no, amount=amount, utr=payment, paid_on=paid_on)
    second = rtgs(demo, job, approved + timedelta(minutes=5), deposit=True, invoice_no=invoice_no, amount=amount,
                  utr=deposit, paid_on=paid_on)
    receipt(demo, job, first, approved + timedelta(hours=4, minutes=17), forwarded=receipt_forwarded)
    receipt(demo, job, second, approved + timedelta(hours=4, minutes=31))
    return {"deposit_utr": deposit, "paid_on": paid_on, "receipts_at": approved + timedelta(hours=4, minutes=31)}


# The stories ------------------------------------------------------------------------


def make_jobs(demo: Demo) -> None:
    by_key = {job.key: job for job in JOBS}
    opened = {
        "A": day(9, "10:05"), "B": day(7, "11:20"), "C": day(6, "09:48"), "D": day(5, "10:32"),
        "E": day(4, "11:02"), "F": day(3, "09:51"), "K": day(2, "10:40"), "G": ago(21), "H": ago(2.6),
        "L": day(34, "10:15"), "M": day(28, "11:40"), "N": day(22, "09:55"), "P": day(15, "10:30"),
    }
    with demo.rt.sessionmaker() as session:
        kept = session.scalar(select(Shipment).where(Shipment.job_no == KEEP_JOB))
        kept_at = service.aware(kept.created_at) if kept else NOW
    prefix, kept_no = KEEP_JOB.rsplit("/", 1)
    before = sorted((k for k in opened if opened[k] < kept_at), key=lambda k: opened[k], reverse=True)
    after = sorted((k for k in opened if opened[k] >= kept_at), key=lambda k: opened[k])
    for n, k in enumerate(before, 1):
        by_key[k].job_no = f"{prefix}/{int(kept_no) - n:04d}"
    for n, k in enumerate(after, 1):
        by_key[k].job_no = f"{prefix}/{int(kept_no) + n:04d}"
    for job in [*JOBS, UNMARKED]:
        job.containers = [_box(random.choice(("CMAU", "ECMU", "TCNU", "TGHU", "CAIU"))) for _ in job.sizes]

    for job in sorted(JOBS, key=lambda j: opened[j.key]):
        print(f"  {job.key} {job.job_no}: {job.story}")
        pre_alert(demo, job, opened[job.key], scanned=job.key == "H")
        renumber(demo, job, opened[job.key])
        story = STORIES[job.key]
        story(demo, job, opened[job.key], by_key)

    print(f"  {UNMARKED.key} (no job): {UNMARKED.story}")
    pre_alert(demo, UNMARKED, ago(1.4), marker=False)


def story_a(demo: Demo, job: Job, opened: datetime, jobs: dict[str, Job]) -> None:
    paid = paid_through_receipts(demo, job, opened)
    records = raise_deposit(demo, job, paid["receipts_at"] + timedelta(hours=17, minutes=40), paid["deposit_utr"], paid["paid_on"])
    approve_deposit(demo, job, records, paid["receipts_at"] + timedelta(days=1, hours=1, minutes=5))
    built = paid["receipts_at"] + timedelta(days=1, hours=18, minutes=40)
    build(demo, job, built)
    dor = request_do(demo, job, built + timedelta(minutes=14))
    issue_edo(demo, job, dor, built + timedelta(days=2, hours=2, minutes=35))


def story_b(demo: Demo, job: Job, opened: datetime, jobs: dict[str, Job]) -> None:
    paid = paid_through_receipts(demo, job, opened)
    records = raise_deposit(demo, job, paid["receipts_at"] + timedelta(hours=16, minutes=55), paid["deposit_utr"], paid["paid_on"])
    approve_deposit(demo, job, records, paid["receipts_at"] + timedelta(days=1, hours=2, minutes=20))
    build(demo, job, day(1, "15:10"))
    request_do(demo, job, day(1, "15:24"))


def story_c(demo: Demo, job: Job, opened: datetime, jobs: dict[str, Job]) -> None:
    paid = paid_through_receipts(demo, job, opened)
    records = raise_deposit(demo, job, paid["receipts_at"] + timedelta(hours=17, minutes=5), paid["deposit_utr"], paid["paid_on"])
    approve_deposit(demo, job, records, paid["receipts_at"] + timedelta(days=1, hours=3, minutes=40))
    build(demo, job, ago(3))


def story_d(demo: Demo, job: Job, opened: datetime, jobs: dict[str, Job]) -> None:
    # CMA CGM's DO Receipt mail went to the team's mailbox and was never forwarded.
    paid = paid_through_receipts(demo, job, opened, receipt_forwarded=False)
    records = raise_deposit(demo, job, paid["receipts_at"] + timedelta(hours=18, minutes=10), paid["deposit_utr"], paid["paid_on"])
    approve_deposit(demo, job, records, paid["receipts_at"] + timedelta(days=1, hours=1, minutes=55))


def story_d_wrong_receipt(demo: Demo, jobs: dict[str, Job]) -> None:
    """Someone attaches a DO Receipt by hand: job E's, not job D's."""
    d, e = jobs["D"], jobs["E"]
    with demo.rt.sessionmaker() as session:
        found = session.scalar(select(Document).where(
            Document.shipment_id == e.id, Document.doc_key == "do_receipt", Document.current.is_(True)))
        name, data = found.original_name, (demo.settings.storage_dir / e.id / found.original_file).read_bytes()
    when = ago(20.5)
    with demo.at(when):
        reply = demo.api.post(f"/api/shipments/{d.id}/slots/do_receipt/file", files={"file": (name, data)})
        if reply.status_code != 200:
            raise SystemExit(f"Attaching the receipt to {d.job_no} failed: {reply.text}")
        service.refresh_folder_review_job(demo.rt, d.id)
    demo.client.sent.clear()


def story_e(demo: Demo, job: Job, opened: datetime, jobs: dict[str, Job]) -> None:
    # Finance answers in its own words: no rule knows them, so Gemini reads the reply.
    payment, deposit = utr(), utr()
    words = (
        "Hi team,\n\nWe have released the payment to CMA CGM today. The DO invoice went by RTGS with reference "
        f"no. {payment}, and the security deposit of Rs 40,000 went separately, reference no. {deposit}.\n\n"
        "Regards,\nAccounts"
    )
    paid = paid_through_receipts(demo, job, opened, finance_body=words)
    raise_deposit(demo, job, ago(19.7), paid["deposit_utr"], paid["paid_on"])


def story_f(demo: Demo, job: Job, opened: datetime, jobs: dict[str, Job]) -> None:
    request = request_invoice(demo, job, opened + timedelta(minutes=2))
    released = opened + timedelta(days=1, hours=5, minutes=20)
    release_invoice(demo, job, request, released)
    mail_finance(demo, job, released + timedelta(minutes=3))
    # Finance replies in the thread, but types the MBL with two digits swapped.
    typo = job.mbl[:5] + job.mbl[6] + job.mbl[5] + job.mbl[7:]
    finance_replies(demo, job, ago(3.3), utr(), utr(), mbl_in_body=typo)


def story_k(demo: Demo, job: Job, opened: datetime, jobs: dict[str, Job]) -> None:
    request_invoice(demo, job, opened + timedelta(minutes=2))


def story_g(demo: Demo, job: Job, opened: datetime, jobs: dict[str, Job]) -> None:
    """The browser agent runs the invoice request for real while ODeX (the local portal) is down."""
    with demo.at(opened + timedelta(minutes=1)):
        result = demo.run(
            "do-invoice-request", EventNames.DOCUMENT_RECEIVED,
            {"shipment_id": job.id, "slot_id": "mbl", "document_id": "", "source": "email"},
        )
    demo.client.sent.clear()
    if result.status is mocked.Status.COMPLETED:
        raise SystemExit("The invoice request worked: stop the local portals so the run fails, then run this again.")


def story_h(demo: Demo, job: Job, opened: datetime, jobs: dict[str, Job]) -> None:
    request_invoice(demo, job, opened + timedelta(minutes=2))


STORIES = {"A": story_a, "B": story_b, "C": story_c, "D": story_d, "E": story_e, "F": story_f, "K": story_k,
           "G": story_g, "H": story_h, "L": story_a, "M": story_a, "N": story_a, "P": story_a}


# Before and after -------------------------------------------------------------------


def listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def back_up(settings: Settings) -> Path:
    data_dir = Path(settings.database_url.removeprefix("sqlite:///")).parent
    target = data_dir / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    target.mkdir(parents=True)
    database = Path(settings.database_url.removeprefix("sqlite:///"))
    if database.is_file():
        shutil.copy2(database, target / "do_folder.db")
    if settings.storage_dir.is_dir():
        shutil.copytree(settings.storage_dir, target / "files")
    if portal_data_dir().is_dir():
        shutil.copytree(portal_data_dir(), target / "local_portals")
    return target


def clean(settings: Settings) -> None:
    """Everything but the kept job: its rows, its files, its portal records, and the mail log."""
    sessionmaker = make_sessionmaker(settings.database_url)
    with sessionmaker() as session:
        kept = session.scalar(select(Shipment).where(Shipment.job_no == KEEP_JOB))
        others = [sid for sid in session.scalars(select(Shipment.id)) if kept is None or sid != kept.id]
        for model in (Event, Task, Document, AutomationRun, IdempotencyKey):
            session.execute(delete(model).where(model.shipment_id.in_(others)))
        session.execute(delete(Shipment).where(Shipment.id.in_(others)))
        for mail in session.scalars(select(InboundMail)):
            if kept is None or kept.id not in (mail.shipment_ids or []):
                session.delete(mail)
        profile = session.get(Profile, PROFILE_ID)
        profile.data = {**profile.data, **{k: v for k, v in DUMMY_REFUND.items() if not profile.data.get(k)}}
        session.flush()
        # The dummy jobs' folders are built days back: fixed details last changed before them.
        session.execute(update(Profile).values(updated_at=NOW - timedelta(days=12)))
        session.commit()
        keep_bls = set()
        if kept is not None:
            keep_bls = {str(kept.data.get("mbl_no") or "").upper(), *[h.upper() for h in str(kept.data.get("hbl_nos") or "").split()]}
    for sid in others:
        shutil.rmtree(settings.storage_dir / sid, ignore_errors=True)
    for folder in ("inbox", "outbox"):
        shutil.rmtree(settings.storage_dir / folder, ignore_errors=True)

    store = portal_store.Store(portal_data_dir())
    with store.change() as data:
        for kind in ("invoice_requests", "rtgs_requests", "deposits", "do_requests"):
            data[kind] = [r for r in data[kind] if str(r.get("bl_no", "")).upper() in keep_bls]
        data["outbox"] = [m for m in data["outbox"] if any(bl in f"{m['subject']} {m['body']}" for bl in keep_bls)]
        data["finance"] = {}
        used = {f for kind in ("invoice_requests", "rtgs_requests", "deposits", "do_requests") for r in data[kind]
                for f in [r.get("invoice_file"), r.get("slip_file"), r.get("edo_file"), *(r.get("receipts") or []),
                          *(a.get("file") for a in r.get("attachments") or [])] if f}
        used |= {f for m in data["outbox"] for f in m.get("attachments", [])}
        for file_id in [f for f in data["files"] if f not in used]:
            (store.files_dir / data["files"].pop(file_id)["stored"]).unlink(missing_ok=True)


def main() -> None:
    global KEEP_JOB
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--keep", default=KEEP_JOB, help=f"the job to keep (default {KEEP_JOB})")
    KEEP_JOB = parser.parse_args().keep
    if listening(8000) or listening(8100):
        raise SystemExit("Stop the demo first: the app (:8000) or the local portals (:8100) are running.")
    settings = Settings.from_env()
    print(f"Backed up to {back_up(settings)}")
    # A database with its tables, the fixed details and the demo sign-in, even on a new machine.
    create_app(replace(settings, seed_demo_data=True), Recorder())
    clean(settings)
    print(f"Removed every job but {KEEP_JOB} (if it is there), and the mail log.")
    demo = Demo()
    print("Making the dummy jobs:")
    make_jobs(demo)
    story_d_wrong_receipt(demo, {job.key: job for job in JOBS})
    with demo.rt.sessionmaker() as session:
        mails = session.scalar(select(func.count()).select_from(InboundMail))
    print(f"Done: {len(JOBS)} dummy jobs, {mails} mails in the mail log.")


if __name__ == "__main__":
    main()
