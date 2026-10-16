"""Reminders and the morning digest: who hears about a step that waits too long, and when.

The timings are the team's table of 16 Oct 2026, one rule for each PROCESS.md step that
waits. A rule mails its contact ``first`` after the step's clock started, then every
``every``, until ``until``; then it escalates once, with the contact in Cc. A reminder
missed while the app was off isn't sent late: only the latest one due goes out.

The clocks are the Control Tower's (process_steps.py), worked out from the database, so a
check every few minutes finds what is due: the local engine forgets its timers when it
restarts. Each mail takes an insert-once claim (workflows/reminders.py), so none goes twice.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import claims, process_steps, service, tasks
from .config import Settings
from .db import IdempotencyKey, Shipment
from .folder import STAGES
from .runtime import Runtime

HOUR = timedelta(hours=1)
# Whom a rule can mail, by their setting (REMINDERS_<KEY>), and how a mail names them.
CONTACTS = {
    "ops_lead": "the ops lead",
    "manager": "the manager",
    "envogue_poc": "Envogue's contact",
    "cma_poc": "CMA CGM's contact",
    "odex_poc": "ODeX's contact",
}
WHO = {"ops": "ops", "finance": "Finance", "cma": "CMA CGM"}
ESCALATION = "escalation"
OVERALL = "2-11"  # the run from the invoice request to the approved DO request


@dataclass(frozen=True)
class Rule:
    step: str  # the PROCESS.md step that waits
    late: str  # what is late, as the mail says it
    first: timedelta
    every: timedelta | None
    until: timedelta
    to: str  # a key of CONTACTS
    escalate_to: str


RULES = (
    Rule("3", "CMA CGM hasn't released the DO invoice on ODeX", 6 * HOUR, 6 * HOUR, 24 * HOUR, "envogue_poc", "ops_lead"),
    Rule("6", "Finance hasn't sent the UTRs", HOUR, 3 * HOUR, 12 * HOUR, "ops_lead", "manager"),
    Rule("7c", "CMA CGM hasn't mailed the receipts", 12 * HOUR, 3 * HOUR, 24 * HOUR, "cma_poc", "ops_lead"),
    Rule("8", "The security deposit isn't raised on ODeX", HOUR, 3 * HOUR, 24 * HOUR, "ops_lead", "ops_lead"),
    Rule("9", "CMA CGM hasn't approved the security deposit on ODeX", HOUR, 3 * HOUR, 24 * HOUR, "odex_poc", "ops_lead"),
    Rule("10", "The DO folder isn't reviewed and built", HOUR, HOUR, 6 * HOUR, "ops_lead", "ops_lead"),
    Rule("11", "The DO request isn't approved", 2 * HOUR, 3 * HOUR, 24 * HOUR, "ops_lead", "manager"),
    Rule(OVERALL, "The DO request isn't approved 12 days after the invoice request", 12 * 24 * HOUR, None,
         12 * 24 * HOUR, "ops_lead", "manager"),
)


def schedule(rule: Rule) -> list[tuple[str, timedelta]]:
    """The mails of a rule, and when each is due after the clock started."""
    mails: list[tuple[str, timedelta]] = []
    at = rule.first
    while rule.every and at < rule.until:
        mails.append((f"reminder {len(mails) + 1}", at))
        at += rule.every
    return [*mails, (ESCALATION, rule.until)]


def clocks(rows: list[dict[str, Any]]) -> dict[str, datetime]:
    """When each waiting step's clock started, by step number, with the whole run's."""
    waiting = (process_steps.WAITING, process_steps.NEEDS_YOU)
    started = {r["number"]: r["since"] for r in rows if r["state"] in waiting and r["since"]}
    by_number = {r["number"]: r for r in rows}
    request, approval = by_number["2"], by_number["11"]
    if request["state"] == process_steps.DONE and request["at"] and approval["state"] != process_steps.DONE:
        started[OVERALL] = request["at"]
    return started


def annotate(rows: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Adds to each waiting step when its next mail goes, and whether it is late: its
    first reminder is due."""
    started = clocks(rows)
    for rule in RULES:
        row = next((r for r in rows if r["number"] == rule.step), None)
        since = started.get(rule.step)
        if row is None or since is None:
            continue
        upcoming = [since + at for _, at in schedule(rule) if since + at > now]
        row["next_mail_at"] = upcoming[0] if upcoming else None
        row["late"] = now - since >= rule.first
    return rows


def steps_of(session: Session, shipment: Shipment) -> list[dict[str, Any]]:
    attached = {d.doc_key if d.hbl is None else f"{d.doc_key}:{d.hbl}" for d in service.current_docs(session, shipment.id)}
    return process_steps.steps_for(shipment, tasks.for_shipment(session, shipment.id), attached)


def due_job(rt: Runtime) -> list[dict[str, Any]]:
    """The reminders and escalations due now on the open jobs, that haven't gone yet."""
    now = datetime.now(timezone.utc)
    mails = []
    with rt.sessionmaker() as session:
        for shipment in session.scalars(select(Shipment)):
            if shipment.status == "edo_received":
                continue
            rows = steps_of(session, shipment)
            started = clocks(rows)
            for rule in RULES:
                since = started.get(rule.step)
                due = [name for name, at in schedule(rule) if since is not None and since + at <= now]
                if not due:
                    continue
                key = f"reminder:{shipment.id}:{rule.step}:{int(since.timestamp())}:{due[-1]}"
                sent = session.get(IdempotencyKey, key)
                if sent is not None and sent.status == claims.DONE:
                    continue
                mail = _mail(rt.settings, shipment, rule, due[-1], since, now, rows)
                if mail is not None:
                    mails.append({"key": key, "shipment_id": shipment.id, **mail})
    return mails


def record_job(rt: Runtime, shipment_id: str, detail: str) -> None:
    with rt.sessionmaker() as session:
        service.log(session, shipment_id, "reminder", detail)
        session.commit()


def digest_job(rt: Runtime) -> dict[str, Any] | None:
    """The morning digest: every open job, the steps that wait and for how long, and
    which are late. None when nobody is set to get it (DIGEST_TO)."""
    to = _split(rt.settings.digest_to)
    if not to:
        return None
    now = datetime.now(timezone.utc)
    stage_names = dict(STAGES)
    open_jobs, needs_you, late, rows_html = 0, 0, 0, []
    with rt.sessionmaker() as session:
        for shipment in session.scalars(select(Shipment).order_by(Shipment.job_no)):
            if shipment.status == "edo_received":
                continue
            open_jobs += 1
            steps = annotate(steps_of(session, shipment), now)
            needs_you += sum(s["state"] == process_steps.NEEDS_YOU for s in steps)
            late += sum(bool(s.get("late")) for s in steps)
            waits = [s for s in steps if s["state"] in (process_steps.WAITING, process_steps.NEEDS_YOU, process_steps.FAILED)]
            if not waits:  # nothing waits on anyone: say what comes next
                waits = [next((s for s in steps if s["state"] != process_steps.DONE), steps[-1])]
            rows_html.append(
                f"<tr><td>{escape(shipment.job_no)}</td><td>{escape(shipment.data.get('mbl_no') or '-')}</td>"
                f"<td>{escape(stage_names[shipment.status])}</td>"
                f"<td>{'<br>'.join(_where(s, now) for s in waits)}</td></tr>"
            )
    cell = "border:1px solid #d9d3ec;padding:6px 8px;text-align:left;vertical-align:top"
    table = (
        f'<table style="border-collapse:collapse;font:14px Helvetica,Arial,sans-serif">'
        f"<tr><th>Job</th><th>MBL</th><th>Stage</th><th>Where it stands</th></tr>{''.join(rows_html)}</table>"
    ).replace("<th>", f'<th style="{cell};background:#efedf8">').replace("<td>", f'<td style="{cell}">')
    today = now.astimezone()
    return {
        "to": to,
        "subject": f"DO digest {today:%d %b}: {open_jobs} open job{'' if open_jobs == 1 else 's'}, "
                   f"{needs_you} need{'s' if needs_you == 1 else ''} you, {late} late",
        "body_html": f"<p>Good morning. The open DO jobs at {today:%H:%M}:</p>{table}<p>DO workflow</p>",
    }


def _where(step: dict[str, Any], now: datetime) -> str:
    if step["state"] == process_steps.NEEDS_YOU:
        state = "needs ops"
    elif step["state"] == process_steps.WAITING:
        state = f"waiting on {WHO[step['waiting_on']]}"
    else:
        state = step["state"].replace("_", " ")
    waited = f", {_waited(now - step['since'])}" if step["since"] else ""
    return escape(f"{step['number']} {step['title']}: {state}{waited}") + (" <b>(late)</b>" if step.get("late") else "")


def _mail(settings: Settings, shipment: Shipment, rule: Rule, name: str, since: datetime, now: datetime,
          rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    escalation = name == ESCALATION
    who = rule.escalate_to if escalation else rule.to
    to = _split(getattr(settings, f"reminders_{who}"))
    if not to:
        return None
    cc = [a for a in _split(getattr(settings, f"reminders_{rule.to}")) if a not in to] if escalation else []
    mbl = shipment.data.get("mbl_no") or "-"
    step = next((r for r in rows if r["number"] == rule.step), None)
    waited = _waited(now - since)
    lines = [
        f"{rule.late}: job {shipment.job_no}, MBL {mbl}.",
        f"It has waited {waited}, since {since.astimezone():%d %b, %H:%M}."
        + (f" {step['note']}" if step and step["note"] else ""),
    ]
    if escalation:
        lines.append(f"The reminders went unanswered, so this goes to {CONTACTS[who]}.")
    return {
        "to": to,
        "cc": cc,
        "subject": f"{'Escalation' if escalation else name.capitalize()}: {shipment.job_no} // {mbl} // {rule.late}",
        "body_html": "".join(f"<p>{escape(line)}</p>" for line in lines) + "<p>DO workflow</p>",
        "history": f"{name.capitalize()} sent to {CONTACTS[who]}: {rule.late} ({waited}).",
    }


def _split(value: str) -> list[str]:
    return [a.strip() for a in value.split(",") if a.strip()]


def _waited(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    hours = minutes // 60
    return f"{hours // 24} d {hours % 24} h" if hours >= 24 else f"{hours} h {minutes % 60} min"
