"""The Inngest client, set up as in python-brain's shared/inngest_client.py."""
from __future__ import annotations

import logging

import inngest

from ..config import Settings

APP_ID = "do-workflow"

logger = logging.getLogger(__name__)


def make_client(settings: Settings) -> inngest.Inngest:
    """Dev mode talks to the local dev server and needs no keys; production needs both."""
    client = inngest.Inngest(
        app_id=APP_ID,
        # The dev server accepts any event key; python-brain forces "dev" the same way.
        event_key=settings.inngest_event_key or ("dev" if settings.inngest_dev else None),
        signing_key=settings.inngest_signing_key,
        is_production=not settings.inngest_dev,
    )
    logger.info("Inngest client ready (app %s, dev mode %s)", APP_ID, settings.inngest_dev)
    return client
