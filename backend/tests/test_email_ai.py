"""The mail reader (app/email_ai.py): a marked mail no rule knows goes to Gemini, and
only what the mail itself states reaches the job. Gemini is replaced at the HTTP transport."""
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import Engine, RecordingInngest, new_shipment, settings_for
from tests.test_email_inbound import ATTACHMENTS, inbox, mail, receive  # noqa: F401 - the fixture

GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/"
SUBJECT = "RE: HXL/IMP/26-27/0001 // SHZ1234567 // CMA // #DOHACK"
# Finance's own wording: no "Invoice UTR:" line for the rules to read.
FINANCE_REPLY = (
    "<p>Hi team,</p><p>Payment released to CMA CGM today. The invoice amount went with UTR ICIC0000123456 "
    "and the security deposit of Rs 40,000 with UTR ICIC0000123457.</p><p>Regards,<br>Accounts</p>"
)


def answer(**reading: object) -> httpx.Response:
    """Gemini's answer: the reading as JSON text, as its structured output gives it."""
    empty = {"mbl_no": "", "hbl_nos": [], "invoice_nos": [], "payment_utr": "", "deposit_utr": "", "deposit_receipt": False}
    text = json.dumps({**empty, "reason": "", **reading})
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


@pytest.fixture()
def reader(tmp_path, private_dir):
    """The app with a Gemini key and two models to try."""
    client = RecordingInngest()
    settings = settings_for(tmp_path, private_dir, google_api_key="test-key", ai_models="model-a,model-b")
    app = create_app(settings, client)
    with TestClient(app) as api:
        yield api, Engine(app, client), client


def finance_reply_arrives(inbox: dict, client: RecordingInngest, engine: Engine) -> None:
    inbox["m1"] = mail(FINANCE_REPLY, subject=SUBJECT)
    ATTACHMENTS["m1"] = []
    receive(client)
    engine.run()


def test_a_reply_no_rule_knows_is_read_by_the_next_free_model(reader, inbox):  # noqa: F811
    # Protects: a marked mail in a wording no rule knows still fills the job, through the model, and a busy model
    # hands over to the next. Fails if the model isn't asked, its reading isn't applied, or a 503 stops the read.
    api, engine, client = reader
    shipment = new_shipment(api, mbl_no="SHZ1234567", hbl_nos="WOLSZSE12345678A", containers="")
    with respx.mock:
        busy = respx.post(f"{GEMINI}model-a:generateContent").mock(return_value=httpx.Response(503, json={}))
        read = respx.post(f"{GEMINI}model-b:generateContent").mock(
            return_value=answer(kind="finance_payment", mbl_no="SHZ1234567", payment_utr="ICIC0000123456",
                                deposit_utr="ICIC0000123457", reason="Finance paid both.")
        )
        finance_reply_arrives(inbox, client, engine)
    assert busy.called and read.called
    sent = read.calls.last.request
    assert sent.headers["x-goog-api-key"] == "test-key"
    assert "Payment released to CMA CGM today" in json.loads(sent.content)["contents"][0]["parts"][0]["text"]
    values = api.get(f"/api/shipments/{shipment['id']}").json()["values"]
    assert (values["payment_utr"], values["deposit_utr"]) == ("ICIC0000123456", "ICIC0000123457")
    history = [e["detail"] for e in api.get(f"/api/shipments/{shipment['id']}/events").json()]
    assert any("model-b read it as Finance's payment reply: invoice UTR …3456, deposit UTR …3457" in d for d in history)


def test_an_identifier_the_mail_does_not_state_never_reaches_the_job(reader, inbox):  # noqa: F811
    # Protects: the model only copies: a UTR the mail doesn't contain is dropped. Fails if a reading is applied
    # without checking it against the mail.
    api, engine, client = reader
    shipment = new_shipment(api, mbl_no="SHZ1234567", hbl_nos="WOLSZSE12345678A", containers="")
    with respx.mock:
        respx.post(f"{GEMINI}model-a:generateContent").mock(
            return_value=answer(kind="finance_payment", mbl_no="SHZ1234567", payment_utr="ICIC0000999999")
        )
        finance_reply_arrives(inbox, client, engine)
    assert not api.get(f"/api/shipments/{shipment['id']}").json()["values"].get("payment_utr")


def test_without_a_key_the_model_is_never_asked(api, engine, client, inbox):  # noqa: F811
    # Protects: with no GOOGLE_API_KEY a marked mail no rule knows is left unread, as before. Fails if the reader
    # runs without a key.
    new_shipment(api, mbl_no="SHZ1234567")
    with respx.mock(assert_all_called=False):
        gemini = respx.post(url__startswith=GEMINI).mock(return_value=answer(kind="other"))
        finance_reply_arrives(inbox, client, engine)
    assert not gemini.called
