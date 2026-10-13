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
    do_set_path: Path
    template_path: Path
    output_dir: Path
    frontend_dist: Path
    job_prefix: str = "HXL/IMP"
    seed_demo_data: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.getenv("DO_DATA_DIR", str(BACKEND_DIR / "data")))
        return cls(
            database_url=os.getenv("DO_DATABASE_URL", f"sqlite:///{data_dir / 'do_workflow.db'}"),
            do_set_path=Path(os.getenv("DO_SET_PATH", str(BACKEND_DIR / "do_set.yaml"))),
            template_path=Path(os.getenv("DO_TEMPLATE_PATH", str(BACKEND_DIR / "templates" / "do_set.docx"))),
            output_dir=Path(os.getenv("DO_OUTPUT_DIR", str(data_dir / "generated"))),
            frontend_dist=Path(os.getenv("DO_FRONTEND_DIST", str(REPO_DIR / "frontend" / "dist"))),
            job_prefix=os.getenv("DO_JOB_PREFIX", "HXL/IMP"),
            seed_demo_data=os.getenv("DO_SEED_DEMO_DATA", "1") != "0",
        )
