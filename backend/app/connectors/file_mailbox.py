"""A mailbox on disk, standing in for Graph when no Outlook credentials are set, so the whole
mail flow can be tried on one machine. scripts/simulate_mail.py writes mail into it, and the
app's own mail (to Finance) goes to its outbox folder."""
from __future__ import annotations

import json
import shutil
import uuid
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

    async def send_email(
        self, to: list[str], subject: str, body_html: str, from_address: str | None = None,
        attachment_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Keeps the mail in this folder instead of sending it, in the shape the inbox reads."""
        message_id = uuid.uuid4().hex
        folder = self.root / message_id
        (folder / "attachments").mkdir(parents=True)
        for path in attachment_paths or []:
            shutil.copy(path, folder / "attachments" / Path(path).name)
        message = {
            "id": message_id,
            "subject": subject,
            "toRecipients": [{"emailAddress": {"address": address}} for address in to],
            "body": {"contentType": "html", "content": body_html},
            "hasAttachments": bool(attachment_paths),
        }
        (folder / "message.json").write_text(json.dumps(message, indent=2))
        return {"id": message_id}
