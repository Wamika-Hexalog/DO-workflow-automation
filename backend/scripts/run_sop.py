"""Runs an SOP with python-brain's browser agent on its own, outside the workflows:
to check the agent works on this machine, to try an SOP while writing it, and to
run a step of the DO process for a job, with the job's own data.

    # An SOP with params given by hand. The run log goes in a throwaway database.
    uv run python scripts/run_sop.py sops/examples/sop_example_login_and_upload.yaml \\
        --param file_path=sops/examples/dummy-upload.txt --show

    # A step of the DO process for a job in the app's database, against the local
    # portals (start them first: make portals). app/portal_params.py builds the
    # params; the run log goes on the job.
    uv run python scripts/run_sop.py --job 0431 --step odex_deposit_new --show

It goes through the same run_sop the workflows use, and prints what the agent
read back. Exit code 0 means every run completed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import inngest  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import portal_params, service  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import AutomationRun, Shipment, make_sessionmaker  # noqa: E402
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


def find_job(session: Session, wanted: str) -> Shipment:
    """A job by its number, the number's last part (0431), or its MBL."""
    wanted = wanted.strip().upper()
    shipments = list(session.scalars(select(Shipment).order_by(Shipment.created_at)))
    for shipment in shipments:
        job_no = shipment.job_no.upper()
        if wanted in (job_no, (shipment.data.get("mbl_no") or "").upper()) or job_no.endswith("/" + wanted):
            return shipment
    raise SystemExit(f"No job {wanted}. The jobs are {', '.join(s.job_no for s in shipments) or 'none'}.")


def unreachable(url: str) -> str | None:
    """Why the site at ``url`` can't be opened, or None if it answers."""
    try:
        urllib.request.urlopen(url, timeout=3).close()
    except urllib.error.HTTPError:
        return None  # it answered, if not with a page
    except (urllib.error.URLError, OSError) as exc:
        return str(getattr(exc, "reason", exc))
    return None


def run_once(
    rt: Runtime, sop: Path, params: dict[str, Any], label: str, max_iterations: int, *, shipment_id: str = "", task_key: str = ""
) -> dict[str, Any] | None:
    """Runs the SOP and prints what happened. Returns what the agent read back, or None if it failed."""
    print(f"\n{label}: running {sop.name} {'headless' if rt.settings.browser_headless else 'in a visible browser'}")
    try:
        result = asyncio.run(
            run_sop(rt, str(sop), params, label, shipment_id=shipment_id, task_key=task_key, max_iterations=max_iterations)
        )
        error = None
    except inngest.NonRetriableError as exc:
        result, error = {}, str(exc)
    with rt.sessionmaker() as session:
        run = session.scalar(select(AutomationRun).order_by(AutomationRun.started_at.desc()))
        if run is not None:
            print(f"Run log: {run.sop_name}, {run.status}, {run.completed_steps} of {run.total_steps} steps in {run.duration_seconds} s")
    if error:
        print(f"The SOP failed: {error}")
        return None
    reading = result.get("extraction_data") or {}
    print("What the agent read back:")
    for key, value in reading.items():
        print(f"  {key}: {value}")
    return reading


def run_step(rt: Runtime, args: argparse.Namespace, sop: Path | None) -> int:
    """One step of the DO process for a job, once per house bill where it's per HBL."""
    step = portal_params.STEPS[args.step]
    with rt.sessionmaker() as session:
        shipment = find_job(session, args.job)
        try:
            hbls = [args.hbl.strip().upper()] if args.hbl and step.per_hbl else portal_params.targets(step, shipment, rt)
            runs = [(hbl, portal_params.params_for(rt, session, shipment, step.key, hbl)) for hbl in hbls]
        except service.NotReady as exc:
            print(f"Job {shipment.job_no} isn't ready for this: {exc}")
            return 1
    site = runs[0][1].get("odex_url") or runs[0][1].get("cma_rtgs_url") or ""
    if reason := unreachable(site):
        print(f"Can't reach {site} ({reason}). Start the local portals with `make portals`.")
        return 1
    ok = True
    for hbl, params in runs:
        label = f"{step.title} for {'HBL ' + hbl if hbl else 'MBL ' + (shipment.data.get('mbl_no') or '')} (job {shipment.job_no})"
        print(f"\n{label}, with:")
        for key, value in params.items():
            print(f"  {key}: {value}")
        reading = run_once(rt, sop or step.sop, params, label, args.max_iterations, shipment_id=shipment.id, task_key=step.key)
        if reading is None:
            ok = False
        elif saved := portal_params.save_readings(rt, shipment.id, step.key, reading):
            print("Put on the job: " + ", ".join(f"{key} = {value}" for key, value in saved.items()))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an SOP, or a step of the DO process for a job, with the browser agent.")
    parser.add_argument("sop", type=Path, nargs="?", help="The SOP YAML file. With --step, replaces the step's own SOP.")
    parser.add_argument(
        "--param", action="append", default=[], metavar="NAME=VALUE", help="Read in the SOP as params.NAME. Repeatable."
    )
    parser.add_argument("--job", help="With --step: the job, by number (or its last part, like 0431) or MBL")
    parser.add_argument("--step", choices=sorted(portal_params.STEPS), help="A step of the DO process to run for --job")
    parser.add_argument("--hbl", help="With a per-house-bill step: only this house bill")
    parser.add_argument("--show", action="store_true", help="Open the browser window so you can watch")
    parser.add_argument(
        "--slow-mo", type=float, default=None, metavar="MS", help="Pause before each action (default 700 with --show)"
    )
    parser.add_argument("--max-iterations", type=int, default=80, help="Also bounds time: 30 s per iteration")
    args = parser.parse_args()

    sop = None
    if args.sop:
        sop = (args.sop if args.sop.is_absolute() else Path.cwd() / args.sop).resolve()
        if not sop.is_file():
            parser.error(f"No SOP at {sop}")
    if args.step and not args.job:
        parser.error("--step needs --job")
    if not args.step and sop is None:
        parser.error("Give an SOP file, or --job and --step")

    slow_mo = args.slow_mo if args.slow_mo is not None else (700 if args.show else 0)
    if slow_mo:
        os.environ["BROWSER_SLOW_MO_MS"] = str(slow_mo)

    settings = replace(Settings.from_env(), browser_headless=not args.show)
    if args.step:
        rt = Runtime(settings, load_folder(settings.folder_path), make_sessionmaker(settings.database_url), make_client(settings))
        return run_step(rt, args, sop)

    with tempfile.TemporaryDirectory() as scratch:
        settings = replace(settings, database_url=f"sqlite:///{scratch}/runs.db", seed_demo_data=False)
        rt = Runtime(settings, load_folder(settings.folder_path), make_sessionmaker(settings.database_url), make_client(settings))
        reading = run_once(rt, sop, parse_params(parser, args.param), sop.stem, args.max_iterations)
        if reading is not None:
            print("\nThe browser agent works.")
        return 0 if reading is not None else 1


if __name__ == "__main__":
    sys.exit(main())
