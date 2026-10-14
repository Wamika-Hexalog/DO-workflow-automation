"""Runs one SOP with python-brain's browser agent on its own, outside the workflows:
to check the agent works on this machine, and to try an SOP while writing it.

    uv run python scripts/run_sop.py sops/examples/sop_example_login_and_upload.yaml \\
        --param file_path=sops/examples/dummy-upload.txt --show

It goes through the same run_sop the DO request workflow uses, keeps the run log
in a throwaway database, and prints what the agent read back. Exit code 0 means
the SOP completed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import inngest  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import AutomationRun, make_sessionmaker  # noqa: E402
from app.folder import load_folder  # noqa: E402
from app.runtime import Runtime  # noqa: E402
from app.workflows.browser import run_sop  # noqa: E402
from app.workflows.client import make_client  # noqa: E402


def parse_params(parser: argparse.ArgumentParser, items: list[str]) -> dict[str, str]:
    params = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name:
            parser.error(f"--param {item}: use NAME=VALUE")
        # A file given relative to backend/, like the SOPs, still works from anywhere.
        candidate = BACKEND_DIR / value
        params[name] = str(candidate) if value and not os.path.isabs(value) and candidate.exists() else value
    return params


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one SOP with the browser agent.")
    parser.add_argument("sop", type=Path, help="The SOP YAML file")
    parser.add_argument(
        "--param", action="append", default=[], metavar="NAME=VALUE", help="Read in the SOP as params.NAME. Repeatable."
    )
    parser.add_argument("--show", action="store_true", help="Open the browser window so you can watch")
    parser.add_argument(
        "--slow-mo", type=float, default=None, metavar="MS", help="Pause before each action (default 700 with --show)"
    )
    parser.add_argument("--max-iterations", type=int, default=80, help="Also bounds time: 30 s per iteration")
    args = parser.parse_args()

    sop = (args.sop if args.sop.is_absolute() else Path.cwd() / args.sop).resolve()
    if not sop.is_file():
        parser.error(f"No SOP at {sop}")
    params = parse_params(parser, args.param)

    slow_mo = args.slow_mo if args.slow_mo is not None else (700 if args.show else 0)
    if slow_mo:
        os.environ["BROWSER_SLOW_MO_MS"] = str(slow_mo)

    if not os.getenv("GOOGLE_API_KEY"):
        # python-brain's agent refuses to start without one, even when no step calls the model.
        os.environ["GOOGLE_API_KEY"] = "not-used-by-playwright-steps"
        print("GOOGLE_API_KEY isn't set, so a placeholder is used. That's fine while every step is a Playwright step.")

    with tempfile.TemporaryDirectory() as scratch:
        settings = replace(
            Settings.from_env(),
            database_url=f"sqlite:///{scratch}/runs.db",
            browser_headless=not args.show,
            seed_demo_data=False,
        )
        rt = Runtime(settings, load_folder(settings.folder_path), make_sessionmaker(settings.database_url), make_client(settings))
        print(f"\nRunning {sop.name} {'in a visible browser' if args.show else 'headless'}\n")
        try:
            result = asyncio.run(run_sop(rt, str(sop), params, sop.stem, max_iterations=args.max_iterations))
            error = None
        except inngest.NonRetriableError as exc:
            result, error = {}, str(exc)

        print()
        with rt.sessionmaker() as session:
            for run in session.query(AutomationRun).all():
                print(
                    f"Run log: {run.sop_name}, {run.status}, {run.completed_steps} of {run.total_steps} steps "
                    f"in {run.duration_seconds} s"
                )
        if error:
            print(f"\nThe SOP failed: {error}")
            return 1
        print("\nWhat the agent read back:")
        for key, value in (result.get("extraction_data") or {}).items():
            print(f"  {key}: {value}")
        print("\nThe browser agent works.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
