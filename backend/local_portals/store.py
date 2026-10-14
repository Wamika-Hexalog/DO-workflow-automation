"""The local portals' state: one JSON file, plus the files people upload and the
portals generate, under backend/data/local_portals/. All of it is demo data, and
Reset on the control panel clears it."""
from __future__ import annotations

import json
import secrets
import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


def _empty() -> dict[str, Any]:
    return {
        "version": 0,  # goes up with every change; the control panel reloads on it
        "sessions": {},  # ODeX login cookie -> {user, company (empty until chosen), at}
        "counters": {},
        "files": {},
        "invoice_requests": [],
        "rtgs_requests": [],
        "deposits": [],
        "do_requests": [],
        "outbox": [],  # the emails CMA CGM would send
        "autoplay": {"on": False, "delay_seconds": 8},
    }


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_file = root / "state.json"
        self.files_dir = root / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _empty()
        return {**_empty(), **saved}

    def _write(self, data: dict[str, Any]) -> None:
        draft = self.state_file.with_suffix(".tmp")
        draft.write_text(json.dumps(data, indent=2), encoding="utf-8")
        draft.replace(self.state_file)

    @contextmanager
    def read(self) -> Iterator[dict[str, Any]]:
        """The state, for reading only, while no one changes it."""
        with self._lock:
            yield self._data

    @contextmanager
    def change(self) -> Iterator[dict[str, Any]]:
        """A copy of the state to change. It is saved when the block ends, and
        dropped if the block raises, so a rejected request leaves no trace."""
        with self._lock:
            draft = deepcopy(self._data)
            yield draft
            draft["version"] += 1
            self._write(draft)
            self._data = draft

    def reset(self) -> None:
        """Back to empty, keeping the autoplay setting."""
        with self._lock:
            fresh = _empty()
            fresh["autoplay"] = self._data["autoplay"]
            fresh["version"] = self._data["version"] + 1
            shutil.rmtree(self.files_dir, ignore_errors=True)
            self.files_dir.mkdir(parents=True, exist_ok=True)
            self._write(fresh)
            self._data = fresh

    def add_file(self, data: dict[str, Any], content: bytes, name: str) -> str:
        """Keeps a file and returns its id. Call inside change()."""
        file_id = secrets.token_hex(8)
        suffix = Path(name).suffix.lower()[:10]
        (self.files_dir / f"{file_id}{suffix}").write_bytes(content)
        data["files"][file_id] = {"name": Path(name).name, "stored": f"{file_id}{suffix}", "size": len(content)}
        return file_id

    def file(self, file_id: str) -> tuple[str, Path] | None:
        """A kept file's name and where it is."""
        with self._lock:
            info = self._data["files"].get(file_id)
        if info is None:
            return None
        return info["name"], self.files_dir / info["stored"]


def next_ref(data: dict[str, Any], prefix: str, width: int) -> str:
    """The next reference of a kind, numbered per day: SD2026101400001."""
    day = f"{prefix}{datetime.now():%Y%m%d}"
    data["counters"][day] = data["counters"].get(day, 0) + 1
    return f"{day}{data['counters'][day]:0{width}d}"


def next_number(data: dict[str, Any], name: str) -> int:
    data["counters"][name] = data["counters"].get(name, 0) + 1
    return data["counters"][name]
