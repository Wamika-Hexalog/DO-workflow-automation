"""Reminders and the morning digest (app/reminders.py, app/workflows/reminders.py): the
team's timings, the mails a waiting step sends, and the digest. Mail goes to the outbox
on disk, as it does without Outlook."""
import json
from datetime import timedelta

import inngest
import pytest
from fastapi.testclient import TestClient

from app import tasks
from app.db import utcnow
from app.main import create_app
from app.reminders import RULES, schedule
from app.workflows.events import EventNames
from tests.conftest import MBL, Engine, RecordingInngest, new_shipment, settings_for, sign_in

HOUR = timedelta(hours=1)


def test_the_teams_table_becomes_reminders_then_one_escalation() -> None:
    # Protects: the team's timings: the first reminder, the repeats inside the window, then one escalation at its
    # end. Fails if a repeat is dropped, one goes past the window, or the escalation goes at the wrong time.
    rules = {rule.step: rule for rule in RULES}
    assert schedule(rules["6"]) == [
        ("reminder 1", 1 * HOUR), ("reminder 2", 4 * HOUR), ("reminder 3", 7 * HOUR), ("reminder 4", 10 * HOUR),
        ("escalation", 12 * HOUR),
    ]
    assert schedule(rules["3"]) == [
        ("reminder 1", 6 * HOUR), ("reminder 2", 12 * HOUR), ("reminder 3", 18 * HOUR), ("escalation", 24 * HOUR),
    ]
    assert schedule(rules["2-11"]) == [("escalation", 12 * 24 * HOUR)]


@pytest.fixture()
def waiting_on_finance(tmp_path, private_dir):
    """A job whose mail to Finance went out: it waits for the UTRs. Yields the API, the
    engine, the job, and a way to set how long ago the mail went."""
    client = RecordingInngest()
    settings = settings_for(
        tmp_path, private_dir, reminders_ops_lead="lead@example.com", reminders_manager="boss@example.com",
        digest_to="desk@example.com",
    )
    app = create_app(settings, client)
    with TestClient(app) as api:
        sign_in(api)
        s = new_shipment(api)
        rt = app.state.rt

        def mailed_finance(ago: timedelta) -> None:
            with rt.sessionmaker() as session:
                tasks.done(session, s["id"], tasks.FINANCE_MAIL, "Mailed the DO invoice to Finance.", {}, by="browser agent")
                tasks.get(session, s["id"], tasks.FINANCE_MAIL).completed_at = utcnow() - ago
                session.commit()

        yield api, Engine(app, client), s, mailed_finance, settings.storage_dir / "outbox"


def sent(outbox) -> list[dict]:
    return sorted((json.loads(p.read_text()) for p in outbox.glob("*/message.json")), key=lambda m: m["subject"])


def check(engine: Engine, event: str = EventNames.REMINDERS_CHECK) -> None:
    engine.client.sent.append(inngest.Event(name=event, data={}))
    engine.run()


def addresses(message: dict, key: str) -> list[str]:
    return [r["emailAddress"]["address"] for r in message.get(key, [])]


def test_a_wait_past_its_time_reminds_once_then_escalates_with_the_contact_in_cc(waiting_on_finance):
    # Protects: a step waiting past its time mails its contact the latest reminder due, never twice, and at the end
    # of the window escalates with the contact in Cc. Fails if a reminder repeats, a missed one goes late, or the
    # escalation leaves the contact out.
    api, engine, s, mailed_finance, outbox = waiting_on_finance
    mailed_finance(ago=4 * HOUR + 30 * timedelta(minutes=1))
    check(engine)
    check(engine)
    [reminder] = sent(outbox)
    assert reminder["subject"] == f"Reminder 2: {s['job_no']} // {MBL} // Finance hasn't sent the UTRs"
    assert addresses(reminder, "toRecipients") == ["lead@example.com"] and addresses(reminder, "ccRecipients") == []
    history = [e["detail"] for e in api.get(f"/api/shipments/{s['id']}/events").json()]
    assert "Reminder 2 sent to the ops lead: Finance hasn't sent the UTRs (4 h 30 min)." in history
    step = next(p for p in api.get(f"/api/shipments/{s['id']}").json()["process"] if p["number"] == "6")
    assert step["late"] and step["next_mail_at"]

    mailed_finance(ago=12 * HOUR + 5 * timedelta(minutes=1))
    check(engine)
    escalation = next(m for m in sent(outbox) if m["subject"].startswith("Escalation"))
    assert addresses(escalation, "toRecipients") == ["boss@example.com"]
    assert addresses(escalation, "ccRecipients") == ["lead@example.com"]


def test_the_digest_lists_each_open_job_and_what_is_late(waiting_on_finance):
    # Protects: the morning digest names each open job, what it waits on and for how long, and marks what is late.
    # Fails if a job is left out or a late wait isn't marked.
    api, engine, s, mailed_finance, outbox = waiting_on_finance
    mailed_finance(ago=2 * HOUR)
    check(engine, EventNames.DIGEST_SEND)
    [digest] = sent(outbox)
    assert addresses(digest, "toRecipients") == ["desk@example.com"]
    assert digest["subject"].endswith("1 open job, 0 need you, 1 late")
    body = digest["body"]["content"]
    assert s["job_no"] in body and "6 Finance pays and sends the UTRs: waiting on Finance, 2 h 0 min <b>(late)</b>" in body
