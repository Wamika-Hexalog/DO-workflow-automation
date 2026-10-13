"""Insert-once claims on side effects outside the app, after python-brain's
IdempotencyKey: take the claim, do the thing, then mark it done or failed.

A claim left ``claimed`` means a run stopped in the middle, so nobody can tell
whether the side effect happened. It blocks a repeat until the run's failure
handler marks it failed, after ops have been told to check by hand.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import IdempotencyKey

CLAIMED, DONE, FAILED = "claimed", "done", "failed"


class InFlight(Exception):
    """Another run holds the claim, or one stopped while holding it."""


def take(session: Session, key: str, shipment_id: str) -> dict[str, Any] | None:
    """Claims ``key``. Returns the earlier result instead when the side effect
    already happened. A failed claim can be taken again: ops chose to retry."""
    row = session.get(IdempotencyKey, key)
    if row is None:
        session.add(IdempotencyKey(key=key, shipment_id=shipment_id, status=CLAIMED, result={}, note=""))
        try:
            session.commit()
        except IntegrityError as exc:  # someone claimed it between the read and the insert
            session.rollback()
            raise InFlight(key) from exc
        return None
    if row.status == DONE:
        return row.result
    if row.status == CLAIMED:
        raise InFlight(key)
    row.status = CLAIMED
    row.note = ""
    session.commit()
    return None


def finish(session: Session, key: str, result: dict[str, Any]) -> None:
    row = session.get(IdempotencyKey, key)
    if row is not None:
        row.status = DONE
        row.result = result
        session.commit()


def fail(session: Session, key: str, note: str) -> None:
    row = session.get(IdempotencyKey, key)
    if row is not None and row.status != DONE:
        row.status = FAILED
        row.note = note
        session.commit()


def fail_open(session: Session, shipment_id: str, prefix: str, note: str) -> list[str]:
    """Releases claims a stopped run left behind. Returns their keys."""
    rows = session.scalars(
        select(IdempotencyKey).where(
            IdempotencyKey.shipment_id == shipment_id,
            IdempotencyKey.key.startswith(prefix, autoescape=True),
            IdempotencyKey.status == CLAIMED,
        )
    ).all()
    for row in rows:
        row.status = FAILED
        row.note = note
    session.commit()
    return [row.key for row in rows]
