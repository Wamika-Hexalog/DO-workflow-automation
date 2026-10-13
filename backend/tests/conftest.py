"""Shared test helpers, including an in-process stand-in for the Inngest dev server."""
from __future__ import annotations

import asyncio
import io
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import inngest
import pytest
from fastapi.testclient import TestClient
from inngest.experimental import mocked
from PIL import Image
from reportlab.pdfgen import canvas

from app import api as api_module
from app.config import BACKEND_DIR, Settings
from app.main import create_app

MBL = "GGZ3193024"
HBL = "MSEC260803047251"


def make_pdf(*pages: str) -> bytes:
    """A PDF standing in for an emailed document: the given line on each page,
    above the kind of boilerplate every real one carries."""
    buffer = io.BytesIO()
    sheet = canvas.Canvas(buffer)
    for text in pages:
        sheet.drawString(72, 720, text)
        sheet.drawString(72, 120, "CMA CGM AGENCIES (INDIA) PVT LTD, ONE INTERNATIONAL CENTRE, MUMBAI 400013")
        sheet.showPage()
    sheet.save()
    return buffer.getvalue()


def make_image() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buffer, "PNG")
    return buffer.getvalue()


class RecordingInngest(mocked.Inngest):
    """Keeps every event the app sends, from the API or from a function's step."""

    def __init__(self) -> None:
        super().__init__(app_id="do-workflow-tests", is_production=False)
        self.sent: list[inngest.Event] = []
        self.down = False  # True: behave like a stopped dev server

    def _record(self, events: inngest.Event | list[inngest.Event]) -> list[str]:
        if self.down:
            raise ConnectionError("never received response while sending events")
        batch = events if isinstance(events, list) else [events]
        self.sent.extend(batch)
        return ["test"] * len(batch)

    def send_sync(self, events, *, skip_middleware: bool = False) -> list[str]:  # noqa: ANN001
        return self._record(events)

    async def send(self, events, *, skip_middleware: bool = False) -> list[str]:  # noqa: ANN001
        return self._record(events)


def _triggers(fn: inngest.Function) -> list[Any]:
    return fn._triggers  # noqa: SLF001 - the test engine reads what the dev server reads


def _matches(trigger: Any, event: inngest.Event) -> bool:
    if getattr(trigger, "event", None) != event.name:
        return False
    if not trigger.expression:
        return True
    found = re.fullmatch(r'event\.data\.(\w+) == "([^"]*)"', trigger.expression)
    assert found, f"the test engine can't evaluate {trigger.expression!r}"
    return str((event.data or {}).get(found[1])) == found[2]


class Engine:
    """Does what the Inngest dev server does, in process: hands each event the app
    sent to the functions it triggers, runs failure handlers, and follows the
    events those functions send until nothing is left."""

    def __init__(self, app: Any, client: RecordingInngest) -> None:
        self.app = app
        self.client = client
        self.runs: list[tuple[str, str, Any]] = []
        self.delivered: list[str] = []  # every event name, consumed or not

    def run(self) -> list[tuple[str, str, Any]]:
        api_module.flush_notices()
        done = []
        while self.client.sent:
            event = self.client.sent.pop(0)
            self.delivered.append(event.name)
            for fn in self.app.state.functions.values():
                if not any(_matches(t, event) for t in _triggers(fn)):
                    continue
                result = mocked.trigger(fn, event, self.client)
                if result.status is mocked.Status.FAILED and fn._opts.on_failure is not None:  # noqa: SLF001
                    failed = inngest.Event(
                        name="inngest/function.failed",
                        data={
                            "error": {"message": str(result.error)},
                            "event": event.model_dump(),
                            "function_id": fn.id,
                        },
                    )
                    asyncio.run(fn._opts.on_failure(SimpleNamespace(event=failed)))  # noqa: SLF001
                done.append((fn.local_id, event.name, result))
        self.runs.extend(done)
        return done

    def sent_names(self) -> list[str]:
        api_module.flush_notices()
        return [e.name for e in self.client.sent]


@pytest.fixture()
def private_dir(tmp_path: Path) -> Path:
    """Stand-ins for the real stamps and bonds, which are kept out of git."""
    private = tmp_path / "private"
    (private / "stamps").mkdir(parents=True)
    for name in ("hexalog", "jai_shri", "envogue"):
        Image.new("RGBA", (120, 120), (40, 40, 200, 160)).save(private / "stamps" / f"{name}.png")
    (private / "bonds.pdf").write_bytes(make_pdf("CLIENT BOND ENVOGUE", "CONSIGNEE BOND HEXALOG"))
    return private


def settings_for(tmp_path: Path, private: Path, seed: bool = False, **overrides: Any) -> Settings:
    values: dict[str, Any] = dict(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        folder_path=BACKEND_DIR / "do_folder.yaml",
        assets_dir=BACKEND_DIR / "assets",
        private_dir=private,
        storage_dir=tmp_path / "files",
        frontend_dist=tmp_path / "no-frontend",
        demo_docs_dir=tmp_path / "no-demo-docs",
        seed_demo_data=seed,
        odex_sop_path=tmp_path / "no-odex-sop.yaml",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture()
def client() -> RecordingInngest:
    return RecordingInngest()


@pytest.fixture()
def app(tmp_path: Path, private_dir: Path, client: RecordingInngest):
    return create_app(settings_for(tmp_path, private_dir), client)


@pytest.fixture()
def api(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def engine(app, client: RecordingInngest) -> Engine:
    return Engine(app, client)


def new_shipment(api: TestClient, **values: Any) -> dict:
    defaults = dict(mbl_no=MBL, hbl_nos=HBL, containers="TCNU5191327", vessel="OOCL SINGAPORE", voyage="0FBI5W1MA")
    return api.post("/api/shipments", json={"values": {**defaults, **values}}).json()


def attach(api: TestClient, shipment: dict, slot_id: str, data: bytes, name: str = "file.pdf") -> dict:
    response = api.post(f"/api/shipments/{shipment['id']}/slots/{slot_id}/file", files={"file": (name, data)})
    assert response.status_code == 200, response.text
    return response.json()


def slot(shipment: dict, slot_id: str) -> dict:
    return next(s for s in shipment["slots"] if s["id"] == slot_id)


def task(shipment: dict, key: str) -> dict | None:
    return next((t for t in shipment["tasks"] if t["key"] == key), None)


def attach_everything(api: TestClient, s: dict) -> dict:
    s = attach(api, s, f"hbl:{HBL}", make_pdf(f"HOUSE BILL MSEC-260803047 -251 B/L {MBL}"))
    s = attach(api, s, "mbl", make_pdf(f"MASTER BILL {MBL} sheet 1", f"MASTER BILL {MBL} sheet 2"))
    s = attach(api, s, "security_receipt", make_pdf(f"SECURITY RECEIPT 40,000 BL {MBL}"))
    s = attach(api, s, f"security_odex_receipt:{HBL}", make_pdf(f"SECURITY DEPOSIT SLIP B/L No. {HBL}"))
    return attach(api, s, "do_receipt", make_pdf(f"DO RECEIPT 54,720.68 BL {MBL}"))


def build(api: TestClient, engine: Engine, shipment: dict) -> dict:
    """Confirms the review, lets the build workflow run, returns the shipment."""
    response = api.post(f"/api/shipments/{shipment['id']}/build", json={"confirmed": True})
    assert response.status_code == 202, response.text
    assert response.json()["building"] is True
    engine.run()
    return api.get(f"/api/shipments/{shipment['id']}").json()
