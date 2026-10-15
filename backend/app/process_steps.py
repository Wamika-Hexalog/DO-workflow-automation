"""The DO process as PROCESS.md numbers it, for one job: every step, who acts on it, and
where it stands, from the job's tasks, fields, documents and stage.

The Process page shows them all. A step the job hasn't reached shows as not started, a
step done before the app ran it (the job's data or stage is already past it) as done
outside the app, and a wait on Finance or CMA CGM, which has no task of its own, as
waiting, with the time it started: the clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import tasks
from .db import Shipment, Task
from .folder import STAGE_IDS, split_list
from .service import aware

DONE, NEEDS_YOU, FAILED, PENDING, WAITING, NOT_STARTED = "done", "needs_you", "failed", "pending", "waiting", "not_started"
OUTSIDE = "Done outside the app."


@dataclass(frozen=True)
class Step:
    number: str
    title: str
    actor: str  # who does it: app, agent, ops, finance or cma
    task_key: str | None = None


def _task_step(number: str, task_key: str, actor: str) -> Step:
    return Step(number, tasks.TITLES[task_key], actor, task_key)


PRE_ALERT = Step("1", "The pre-alert opens the job", "app")
INVOICE_REQUEST = _task_step("2", tasks.ODEX_INVOICE_REQUEST, "agent")
INVOICE_RELEASE = Step("3", "CMA CGM releases the DO invoice on ODeX", "cma")
INVOICE_COLLECT = _task_step("4", tasks.ODEX_INVOICE_COLLECT, "agent")
FINANCE_MAIL = _task_step("5", tasks.FINANCE_MAIL, "app")
UTRS = Step("6", "Finance pays and sends the UTRs", "finance")
RTGS_PAYMENT = _task_step("7a", tasks.CMA_RTGS_PAYMENT, "ops")
RTGS_DEPOSIT = _task_step("7b", tasks.CMA_RTGS_DEPOSIT, "ops")
RECEIPTS = Step("7c", "CMA CGM mails the receipts", "cma")
ODEX_DEPOSIT = _task_step("8", tasks.ODEX_DEPOSIT, "ops")
DEPOSIT_APPROVAL = Step("9", "CMA CGM approves the deposit on ODeX", "cma")
FOLDER_REVIEW = _task_step("10", tasks.FOLDER_REVIEW, "ops")
DO_REQUEST = _task_step("11", tasks.ODEX_DO_REQUEST, "ops")
EDO = Step("12", "CMA CGM issues the eDO, and the agent collects it", "cma", tasks.ODEX_EDO)


def _row(step: Step, state: str, note: str = "", *, at: datetime | None = None, by: str | None = None,
         waiting_on: str | None = None, since: datetime | None = None) -> dict[str, Any]:
    return {
        "number": step.number, "title": step.title, "actor": step.actor, "task_key": step.task_key, "state": state,
        "note": note, "at": at, "by": by, "waiting_on": waiting_on, "since": since,
    }


def _task_row(step: Step, task: Task | None, done_outside: bool) -> dict[str, Any]:
    """A step the app runs as a task. With no task yet, it is done outside the app when
    the job is already past it, and not started otherwise."""
    if task is None:
        return _row(step, DONE, OUTSIDE) if done_outside else _row(step, NOT_STARTED)
    since = aware(task.status_since or task.updated_at)
    if task.status == tasks.COMPLETED:
        return _row(step, DONE, task.note, at=aware(task.completed_at), by=task.completed_by)
    if task.status == tasks.AWAITING_APPROVAL:
        return _row(step, NEEDS_YOU, task.note, waiting_on="ops", since=since)
    if task.status == tasks.FAILED:
        return _row(step, FAILED, task.note, waiting_on="ops", since=since)
    return _row(step, PENDING, task.note, since=since)


def _wait_row(step: Step, *, done: bool, done_note: str, started: datetime | None, waiting_note: str) -> dict[str, Any]:
    """A wait on Finance or CMA CGM: its clock runs from the end of the step before it."""
    if done:
        return _row(step, DONE, done_note)
    if started is not None:
        return _row(step, WAITING, waiting_note, waiting_on=step.actor, since=started)
    return _row(step, NOT_STARTED)


def steps_for(shipment: Shipment, task_list: list[Task], attached: set[str]) -> list[dict[str, Any]]:
    """Every step of the job, in order. ``attached``: the ids of the folder's slots that have a file."""
    by_key = {t.task_key: t for t in task_list}
    data = shipment.data
    stage = STAGE_IDS.index(shipment.status)

    def past(stage_id: str) -> bool:
        return stage >= STAGE_IDS.index(stage_id)

    def finished(task_key: str) -> datetime | None:
        task = by_key.get(task_key)
        return aware(task.completed_at) if task is not None and task.status == tasks.COMPLETED else None

    invoice_no = data.get("do_invoice_no")
    utrs = [data.get("payment_utr"), data.get("deposit_utr")]
    receipts = [name for slot, name in (("do_receipt", "DO Receipt"), ("security_receipt", "Security Receipt"))
                if slot not in attached]
    hbls = split_list(data.get("hbl_nos"))
    slips = [hbl for hbl in hbls if f"security_odex_receipt:{hbl}" not in attached]
    confirmed = [moment for moment in (finished(tasks.CMA_RTGS_PAYMENT), finished(tasks.CMA_RTGS_DEPOSIT)) if moment]

    rows = [
        _row(PRE_ALERT, DONE, f"Job {shipment.job_no} opened.", at=aware(shipment.created_at)),
        _task_row(INVOICE_REQUEST, by_key.get(tasks.ODEX_INVOICE_REQUEST), bool(invoice_no) or past("invoice_paid")),
        _wait_row(
            INVOICE_RELEASE, done=bool(invoice_no) or tasks.ODEX_INVOICE_COLLECT in by_key or past("invoice_paid"),
            done_note=f"Invoice {invoice_no} is released." if invoice_no else OUTSIDE,
            started=finished(tasks.ODEX_INVOICE_REQUEST), waiting_note="Waiting for CMA CGM to release the DO invoice on ODeX.",
        ),
        _task_row(INVOICE_COLLECT, by_key.get(tasks.ODEX_INVOICE_COLLECT),
                  bool(invoice_no and data.get("do_invoice_amount")) or past("invoice_paid")),
        _task_row(FINANCE_MAIL, by_key.get(tasks.FINANCE_MAIL), bool(utrs[0]) or past("invoice_paid")),
        _wait_row(
            UTRS, done=all(utrs) or past("deposit_approved"), done_note="Both UTRs are in.",
            started=finished(tasks.FINANCE_MAIL),
            waiting_note="Waiting for Finance's UTRs" + (": 1 of 2 is in." if any(utrs) else "."),
        ),
        _task_row(RTGS_PAYMENT, by_key.get(tasks.CMA_RTGS_PAYMENT), past("invoice_paid")),
        _task_row(RTGS_DEPOSIT, by_key.get(tasks.CMA_RTGS_DEPOSIT), past("deposit_approved")),
        _wait_row(
            RECEIPTS, done=not receipts, done_note="Both receipts are in the DO folder.", started=min(confirmed, default=None),
            waiting_note=f"Waiting for CMA CGM's {' and '.join(receipts)}.",
        ),
        _task_row(ODEX_DEPOSIT, by_key.get(tasks.ODEX_DEPOSIT), past("deposit_approved")),
        _wait_row(
            DEPOSIT_APPROVAL, done=past("deposit_approved") or bool(hbls and not slips),
            done_note="ODeX's security slip is in for every house bill.", started=finished(tasks.ODEX_DEPOSIT),
            waiting_note=f"Waiting for CMA CGM to approve the deposit: no slip yet for HBL {', '.join(slips)}.",
        ),
        _task_row(FOLDER_REVIEW, by_key.get(tasks.FOLDER_REVIEW), past("folder_ready")),
        _task_row(DO_REQUEST, by_key.get(tasks.ODEX_DO_REQUEST), past("do_requested")),
    ]
    edo_task = by_key.get(tasks.ODEX_EDO)
    if past("edo_received"):
        rows.append(_row(EDO, DONE, edo_task.note if edo_task else OUTSIDE,
                         at=aware(edo_task.completed_at) if edo_task else None))
    elif edo_task is not None:
        rows.append(_task_row(EDO, edo_task, False))
    else:
        rows.append(_wait_row(EDO, done=False, done_note="", started=finished(tasks.ODEX_DO_REQUEST),
                              waiting_note="Waiting for CMA CGM to issue the eDO."))
    return rows
