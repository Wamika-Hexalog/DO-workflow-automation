"""The assistant (app/assistant.py): Gemini answers the team's questions from what the app
records, through read-only tools. Gemini is replaced at the HTTP transport; the app, its
database and the mail workflow are real."""
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import MBL, Engine, RecordingInngest, new_shipment, settings_for, sign_in
from tests.test_email_inbound import inbox, mail, receive  # noqa: F401 - the fixture

GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/"


def calls(name: str, **args: str) -> httpx.Response:
    """Gemini asks for one tool, with a thought signature it will check when the turn comes back."""
    part = {"functionCall": {"id": "call-1", "name": name, "args": args}, "thoughtSignature": "signed"}
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [part]}}]})


def says(text: str) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]})


def tool_result(request: httpx.Request) -> dict:
    """What the app sent the model for its last tool call."""
    return json.loads(request.content)["contents"][-1]["parts"][0]["functionResponse"]["response"]["result"]


@pytest.fixture()
def assistant(tmp_path, private_dir):
    client = RecordingInngest()
    settings = settings_for(tmp_path, private_dir, google_api_key="test-key", ai_models="model-a,model-b")
    app = create_app(settings, client)
    with TestClient(app) as api:
        sign_in(api)
        yield api, Engine(app, client), client


def test_the_answer_comes_from_the_job_the_model_looked_up_and_a_busy_model_hands_over(assistant):
    # Protects: the model gets the job's real state when it asks for it, its turn goes back as it came (Gemini
    # checks the signature), and a busy model hands the chat to the next. Fails if a tool call goes unanswered,
    # the job's steps don't reach the model, the model's turn is rebuilt, or a 503 ends the chat.
    api, _, _ = assistant
    s = new_shipment(api)
    with respx.mock:
        respx.post(f"{GEMINI}model-a:generateContent").mock(return_value=httpx.Response(503, json={}))
        route = respx.post(f"{GEMINI}model-b:generateContent").mock(
            side_effect=[calls("job_status", job=MBL), says("It waits for the invoice request.")]
        )
        reply = api.post("/api/assistant", json={"question": "Where is it?", "shipment_id": s["id"]}).json()
    assert reply == {"answer": "It waits for the invoice request.", "model": "model-b", "checked": [f"where the job stands ({MBL})"]}
    first, second = (call.request for call in route.calls)
    assert f"page of job {s['job_no']}" in json.loads(first.content)["contents"][-1]["parts"][0]["text"]
    result = tool_result(second)
    assert (result["job"], result["stage"]) == (s["job_no"], "Invoice requested")
    assert [step["step"] for step in result["steps"]][:4] == ["1", "2", "3", "4"]
    turn, answer = json.loads(second.content)["contents"][-2:]
    assert turn["parts"][0]["thoughtSignature"] == "signed"
    assert answer["parts"][0]["functionResponse"]["id"] == "call-1"


def test_every_mail_is_logged_with_what_it_did_or_why_it_did_nothing(assistant, inbox):  # noqa: F811
    # Protects: the mail log the assistant reads to say why a mail started nothing: a mail with no marker is
    # logged as ignored, and a pre-alert with the job it opened. Fails if a path of the mail workflow skips the
    # log, or the log loses the reason or the job.
    api, engine, client = assistant
    inbox["m1"] = mail("Please find the invoice attached.", subject="Invoice")
    inbox["m2"] = {**mail(f"DOHACK:document:{MBL}", subject="Pre-alert"), "id": "m2"}
    receive(client, "m1")
    receive(client, "m2")
    engine.run()
    with respx.mock:
        route = respx.post(f"{GEMINI}model-a:generateContent").mock(side_effect=[calls("mail_log"), says("Ignored.")])
        api.post("/api/assistant", json={"question": "Why did my invoice mail do nothing?"})
    mails = {m["subject"]: m for m in tool_result(route.calls.last.request)["mails"]}
    assert (mails["Invoice"]["read_by"], mails["Invoice"]["outcome"]) == (
        "not read", "Ignored: it has neither the #DOHACK marker nor a DOHACK code."
    )
    [job] = api.get("/api/shipments").json()
    assert (mails["Pre-alert"]["read_by"], mails["Pre-alert"]["jobs"]) == ("code", [job["job_no"]])
    assert mails["Pre-alert"]["outcome"].startswith(f"Opened a job for MBL {MBL}.")


def test_utrs_reach_the_model_as_their_last_4_digits(assistant):
    # Protects: a full UTR never goes to Gemini; the model sees its last 4 digits. Fails if a tool result
    # leaves unmasked.
    api, _, _ = assistant
    new_shipment(api, payment_utr="ICIC0000123456")
    with respx.mock:
        route = respx.post(f"{GEMINI}model-a:generateContent").mock(
            side_effect=[calls("job_status", job=MBL), says("Paid.")]
        )
        api.post("/api/assistant", json={"question": "Which UTR paid it?"})
    request = route.calls.last.request
    assert b"ICIC0000123456" not in request.content
    assert tool_result(request)["values"]["UTR for the shipment payment"] == "…3456"


def test_the_health_check_names_what_is_down_and_the_fixed_details_left_empty(assistant):
    # Protects: the health tool tells the model that the engine and the portals don't answer and which fixed
    # details are empty, so the answer can name the cause. Fails if a stopped engine reads as up, or a check
    # is dropped.
    api, _, _ = assistant
    with respx.mock:
        respx.route(host="127.0.0.1").mock(side_effect=httpx.ConnectError("refused"))
        route = respx.post(f"{GEMINI}model-a:generateContent").mock(side_effect=[calls("system_health"), says("Down.")])
        api.post("/api/assistant", json={"question": "Is the app up?"})
    health = tool_result(route.calls.last.request)
    assert health["workflow_engine"].startswith("down") and health["local_portals"].startswith("down")
    assert "Finance's email for the DO invoice" in health["fixed_details_missing"]
