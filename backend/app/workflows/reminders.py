"""Reminders for the steps that wait too long, and the morning digest (app/reminders.py).

    every 10 minutes, or customs/do.reminders.check → do-reminders → the reminders due
    09:30 IST each day, or customs/do.digest.send  → do-digest   → the digest mail
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import inngest

from .. import claims, reminders
from ..runtime import Runtime
from .events import EventNames
from .invoice import make_sender
from .steps import job


def _send_once(rt: Runtime, mail: dict[str, Any]) -> Callable[[], Awaitable[dict[str, Any]]]:
    """Step handler: sends one reminder, never twice, and notes it on the job."""

    def settle(note: str) -> None:
        with rt.sessionmaker() as session:
            if note:
                claims.fail(session, mail["key"], note)
            else:
                claims.finish(session, mail["key"], {"to": mail["to"]})
        if not note:
            reminders.record_job(rt, mail["shipment_id"], mail["history"])

    async def send() -> dict[str, Any]:
        def take() -> dict[str, Any] | None:
            with rt.sessionmaker() as session:
                return claims.take(session, mail["key"], mail["shipment_id"])

        try:
            if await asyncio.to_thread(take) is not None:
                return {"sent": False, "why": "sent already"}
        except claims.InFlight:
            return {"sent": False, "why": "another check is sending it"}
        try:
            await make_sender(rt).send_email(mail["to"], mail["subject"], mail["body_html"], cc=mail["cc"])
        except Exception as exc:  # the next check tries again: a failed claim can be taken again
            await asyncio.to_thread(settle, f"Not sent: {exc}")
            raise
        await asyncio.to_thread(settle, "")
        return {"sent": True, "to": mail["to"]}

    return send


def create_reminders_function(rt: Runtime) -> inngest.Function:
    @rt.inngest.create_function(
        fn_id="do-reminders",
        name="Reminders: chase the steps that wait too long",
        trigger=[inngest.TriggerCron(cron="*/10 * * * *"), inngest.TriggerEvent(event=EventNames.REMINDERS_CHECK)],
        concurrency=[inngest.Concurrency(limit=1, scope="env", key='"reminders"')],
        retries=1,
    )
    async def remind(ctx: inngest.Context) -> dict:
        mails = await ctx.step.run("find-due", job(reminders.due_job, rt))
        sent = [await ctx.step.run(f"send-{mail['key']}", _send_once(rt, mail)) for mail in mails]
        return {"due": len(mails), "sent": sum(1 for s in sent if s["sent"])}

    return remind


def create_digest_function(rt: Runtime) -> inngest.Function:
    @rt.inngest.create_function(
        fn_id="do-digest",
        name="Digest: every open job, each morning",
        trigger=[inngest.TriggerCron(cron="TZ=Asia/Kolkata 30 9 * * *"), inngest.TriggerEvent(event=EventNames.DIGEST_SEND)],
        retries=1,
    )
    async def digest(ctx: inngest.Context) -> dict:
        mail = await ctx.step.run("write-digest", job(reminders.digest_job, rt))
        if mail is None:
            return {"sent": False, "why": "DIGEST_TO is empty"}

        async def send() -> dict[str, Any]:
            message = await make_sender(rt).send_email(mail["to"], mail["subject"], mail["body_html"])
            return {"id": (message or {}).get("id")}

        return {"sent": True, **await ctx.step.run("send-digest", send)}

    return digest
