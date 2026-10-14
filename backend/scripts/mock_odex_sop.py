"""Writes the mock ODeX SOP for `make demo-agent`, pointed at the mock New DO Request
page the tests use, and prints its path.

    python scripts/mock_odex_sop.py

With DO_ODEX_SOP set to that path, approving a DO request in the app runs the
browser agent against the mock page instead of ODeX. It's a local file: nothing
leaves the machine.
"""
from __future__ import annotations

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND_DIR / "tests" / "fixtures" / "mock_odex"
TARGET = BACKEND_DIR / "data" / "mock_odex" / "sop_mock_odex_do_request.yaml"


def main() -> None:
    page = (FIXTURES / "new_do_request.html").resolve().as_uri()
    template = (FIXTURES / "sop_mock_odex_do_request.yaml").read_text(encoding="utf-8")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(template.replace("__PORTAL_URL__", page), encoding="utf-8")
    print(TARGET)


if __name__ == "__main__":
    main()
