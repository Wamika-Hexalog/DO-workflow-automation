"""Runtime settings, read from environment variables with local defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


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

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.getenv("DO_DATA_DIR", str(BACKEND_DIR / "data")))
        assets_dir = Path(os.getenv("DO_ASSETS_DIR", str(BACKEND_DIR / "assets")))
        return cls(
            database_url=os.getenv("DO_DATABASE_URL", f"sqlite:///{data_dir / 'do_folder.db'}"),
            folder_path=Path(os.getenv("DO_FOLDER_PATH", str(BACKEND_DIR / "do_folder.yaml"))),
            assets_dir=assets_dir,
            private_dir=Path(os.getenv("DO_PRIVATE_DIR", str(assets_dir / "private"))),
            storage_dir=Path(os.getenv("DO_STORAGE_DIR", str(data_dir / "files"))),
            frontend_dist=Path(os.getenv("DO_FRONTEND_DIST", str(REPO_DIR / "frontend" / "dist"))),
            demo_docs_dir=Path(os.getenv("DO_DEMO_DOCS_DIR", str(REPO_DIR / "Docs" / "Docs"))),
            job_prefix=os.getenv("DO_JOB_PREFIX", "HXL/IMP"),
            seed_demo_data=os.getenv("DO_SEED_DEMO_DATA", "1") != "0",
        )
