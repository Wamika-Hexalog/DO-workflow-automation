"""Runtime settings, read from environment variables with local defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() not in ("0", "false", "no", "")


# Where the browser agent may go unless told otherwise: this machine only, which
# is where the local versions of ODeX and the CMA RTGS site run.
LOCAL_HOSTS = ("127.0.0.1", "localhost")

# The local ODeX's login (make portals). Not a secret: that portal runs only on
# this machine. Its SOPs read it from these variables, as the real ones will read
# ODEX_USERNAME and ODEX_PASSWORD.
LOCAL_ODEX_LOGIN = {"LOCAL_ODEX_USERNAME": "hexalog-demo", "LOCAL_ODEX_PASSWORD": "local-demo-only"}


def _hosts(name: str) -> tuple[str, ...] | None:
    """A comma-separated host list. "*" lifts the restriction."""
    value = os.getenv(name)
    if value is None:
        return LOCAL_HOSTS
    if value.strip() == "*":
        return None
    return tuple(h.strip().lower() for h in value.split(",") if h.strip())


@dataclass(frozen=True)
class Settings:
    database_url: str
    folder_path: Path  # do_folder.yaml
    assets_dir: Path  # letterhead artwork, kept in git
    private_dir: Path  # stamps and bonds, kept out of git
    storage_dir: Path  # uploaded files and built PDFs
    frontend_dist: Path
    demo_docs_dir: Path  # sample documents attached to the demo shipments
    job_prefix: str = "HXL/IMP"
    seed_demo_data: bool = True

    # Inngest, with python-brain's variable names. Dev mode talks to the local dev
    # server (`make dev` starts it); production needs both keys.
    inngest_dev: bool = True
    inngest_event_key: str | None = None
    inngest_signing_key: str | None = None
    inngest_serve_origin: str | None = None

    # The browser agent. Without an ODeX SOP file the DO request is raised by hand
    # and ops confirm it in the app.
    odex_sop_path: Path = BACKEND_DIR / "sops" / "odex" / "sop_odex_do_request_new.yaml"
    browser_headless: bool = True
    # Hosts the agent may open; anything else is blocked. None: no restriction.
    browser_allowed_hosts: tuple[str, ...] | None = LOCAL_HOSTS
    # The portals the SOPs open. The defaults are the local versions (make portals).
    odex_url: str = "http://127.0.0.1:8100/odex"
    cma_rtgs_url: str = "http://127.0.0.1:8100/cma-rtgs"
    # Run the steps before and after the DO request with the browser agent as their mail
    # arrives: request and collect the invoice, mail it to Finance, confirm the payments,
    # raise the deposit, collect the eDO. Off: ops do them by hand, as before.
    automate_portals: bool = False

    # Outlook / Microsoft Graph, python-brain's variable names. The hackathon mock flow reads
    # only mail whose body carries "<inject_code_prefix>:<stage>:<MBL>[:<arg>]".
    outlook_client_id: str = ""
    outlook_client_secret: str = ""
    outlook_tenant_id: str = ""
    outlook_recipient_email: str = ""
    outlook_api_timeout: int = 30
    # Public URL of this API (a tunnel that forwards only /api/webhooks/*); Graph calls it back.
    webhook_base_url: str = ""
    # Echoed by Graph in every notification; the webhook drops a mismatch.
    graph_client_state: str = ""
    inject_code_prefix: str = "DOHACK"
    # Mail with this short tag in the subject or body is read for its facts (app/email_infer.py).
    # Empty: any mail the recognizers know is read.
    mail_marker: str = "#DOHACK"
    # House bill numbers to look for in an HBL file. Add the format of each forwarder you receive from.
    hbl_pattern: str = r"(?:WOLSZSE\d{8}[A-Z]|MSEC\d{12})"
    # Logins for the public tunnel, "user:password,user2:password2". Local requests need none.
    basic_auth_users: str = ""

    @property
    def basic_auth_logins(self) -> tuple[tuple[str, str], ...]:
        pairs = (entry.split(":", 1) for entry in self.basic_auth_users.split(",") if ":" in entry)
        return tuple((user.strip(), password) for user, password in pairs if user.strip() and password)

    @property
    def odex_automated(self) -> bool:
        return self.odex_sop_path.is_file()

    @classmethod
    def from_env(cls) -> Settings:
        # Secrets such as GOOGLE_API_KEY and the ODeX login live in backend/.env
        # (see .env.example). Variables already set in the environment win.
        load_dotenv(BACKEND_DIR / ".env", override=False)
        for name, value in LOCAL_ODEX_LOGIN.items():
            os.environ.setdefault(name, value)
        data_dir = Path(os.getenv("DO_DATA_DIR", str(BACKEND_DIR / "data")))
        assets_dir = Path(os.getenv("DO_ASSETS_DIR", str(BACKEND_DIR / "assets")))
        default_sop = cls.__dataclass_fields__["odex_sop_path"].default
        return cls(
            database_url=os.getenv("DO_DATABASE_URL", f"sqlite:///{data_dir / 'do_folder.db'}"),
            folder_path=Path(os.getenv("DO_FOLDER_PATH", str(BACKEND_DIR / "do_folder.yaml"))),
            assets_dir=assets_dir,
            private_dir=Path(os.getenv("DO_PRIVATE_DIR", str(assets_dir / "private"))),
            storage_dir=Path(os.getenv("DO_STORAGE_DIR", str(data_dir / "files"))),
            frontend_dist=Path(os.getenv("DO_FRONTEND_DIST", str(REPO_DIR / "frontend" / "dist"))),
            demo_docs_dir=Path(os.getenv("DO_DEMO_DOCS_DIR", str(REPO_DIR / "Docs" / "Docs"))),
            job_prefix=os.getenv("DO_JOB_PREFIX", "HXL/IMP"),
            seed_demo_data=_flag("DO_SEED_DEMO_DATA", True),
            inngest_dev=_flag("INNGEST_DEV", True),
            inngest_event_key=os.getenv("INNGEST_EVENT_KEY") or None,
            inngest_signing_key=os.getenv("INNGEST_SIGNING_KEY") or None,
            inngest_serve_origin=os.getenv("INNGEST_SERVE_ORIGIN") or None,
            odex_sop_path=Path(os.getenv("DO_ODEX_SOP", str(default_sop))),
            browser_headless=_flag("DO_BROWSER_HEADLESS", True),
            browser_allowed_hosts=_hosts("DO_BROWSER_ALLOWED_HOSTS"),
            odex_url=os.getenv("DO_ODEX_URL", cls.odex_url).rstrip("/"),
            cma_rtgs_url=os.getenv("DO_CMA_RTGS_URL", cls.cma_rtgs_url).rstrip("/"),
            automate_portals=_flag("DO_AUTOMATE_PORTALS", False),
            outlook_client_id=os.getenv("OUTLOOK_CLIENT_ID", ""),
            outlook_client_secret=os.getenv("OUTLOOK_CLIENT_SECRET", ""),
            outlook_tenant_id=os.getenv("OUTLOOK_TENANT_ID", ""),
            outlook_recipient_email=os.getenv("OUTLOOK_RECIPIENT_EMAIL", ""),
            outlook_api_timeout=int(os.getenv("OUTLOOK_API_TIMEOUT", "30")),
            webhook_base_url=os.getenv("WEBHOOK_BASE_URL", "").rstrip("/"),
            graph_client_state=os.getenv("GRAPH_CLIENT_STATE", ""),
            inject_code_prefix=os.getenv("INJECT_CODE_PREFIX", "DOHACK"),
            mail_marker=os.getenv("MAIL_MARKER", cls.mail_marker),
            hbl_pattern=os.getenv("HBL_PATTERN", cls.hbl_pattern),
            basic_auth_users=os.getenv("BASIC_AUTH_USERS", ""),
        )
