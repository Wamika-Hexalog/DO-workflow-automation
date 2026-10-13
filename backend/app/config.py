"""Runtime settings, read from environment variables with local defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() not in ("0", "false", "no", "")


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

    @property
    def odex_automated(self) -> bool:
        return self.odex_sop_path.is_file()

    @classmethod
    def from_env(cls) -> Settings:
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
        )
