"""Settings of the local portals, from environment variables (and backend/.env)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from app.config import BACKEND_DIR, LOCAL_ODEX_LOGIN

load_dotenv(BACKEND_DIR / ".env", override=False)

# Whose ODeX account the local ODeX pretends to be. Printed on the deposit slip
# and the eDO, as "From M/s" and the party the delivery order is issued to.
ACCOUNT_NAME = os.getenv("LOCAL_ODEX_ACCOUNT", "DEMO CHA ACCOUNT (LOCAL ODEX)")


def real_invoices_dir() -> Path | None:
    """Where a demo shipment's real liner invoices are (LOCAL_ODEX_INVOICES_DIR): the local
    ODeX releases the one that names the B/L instead of a demo invoice. Kept out of git."""
    folder = os.getenv("LOCAL_ODEX_INVOICES_DIR", "").strip()
    return Path(folder) if folder else None


def data_dir() -> Path:
    return Path(os.getenv("DO_DATA_DIR", str(BACKEND_DIR / "data"))) / "local_portals"


def screens_dir() -> Path:
    """The ODeX screenshots `make assets` takes from the team's guide."""
    private = Path(os.getenv("DO_PRIVATE_DIR", str(Path(os.getenv("DO_ASSETS_DIR", str(BACKEND_DIR / "assets"))) / "private")))
    return private / "odex"


def odex_login() -> tuple[str, str]:
    """The local ODeX's username and password: the same variables its SOPs read."""
    return (
        os.getenv("LOCAL_ODEX_USERNAME") or LOCAL_ODEX_LOGIN["LOCAL_ODEX_USERNAME"],
        os.getenv("LOCAL_ODEX_PASSWORD") or LOCAL_ODEX_LOGIN["LOCAL_ODEX_PASSWORD"],
    )


def default_login() -> bool:
    """True while the login is the published demo one, so pages may show it."""
    return odex_login() == (LOCAL_ODEX_LOGIN["LOCAL_ODEX_USERNAME"], LOCAL_ODEX_LOGIN["LOCAL_ODEX_PASSWORD"])
