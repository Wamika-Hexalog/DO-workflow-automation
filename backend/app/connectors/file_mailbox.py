"""A mailbox on disk, standing in for Graph when no Outlook credentials are set, so the whole
mail flow can be tried on one machine. scripts/simulate_mail.py writes mail into it."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


class FileMailbox:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def get_message_detail(self, message_id: str) -> dict[str, Any]:
        return json.loads((self.root / message_id / "message.json").read_text())

    async def download_attachments(self, message_id: str, storage_path: str | Path) -> list[dict[str, Any]]:
        target = Path(storage_path)
        target.mkdir(parents=True, exist_ok=True)
        out = []
        for source in sorted((self.root / message_id / "attachments").glob("*")):
            shutil.copy(source, target / source.name)
            out.append({"name": source.name, "path": str(target / source.name)})
        return out
