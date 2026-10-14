"""Drops a mail in the local mailbox and tells the engine it arrived, as Graph would.
Works with no Outlook credentials (the app then reads mail from backend/data/files/inbox).
Needs the API and the Inngest dev server running (make dev).

    uv run python scripts/simulate_mail.py --subject "Fw: ODeX: Security slip" \\
        --code document:SHZ1234567:security_odex_receipt:WOLSZSE12345678A --attach slip.pdf

--code is the part after the prefix and may be repeated; each --attach goes to the next
document code that names a slot, and files for a code with no slot are routed by file name.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inngest  # noqa: E402

from app.config import Settings  # noqa: E402
from app.workflows.client import make_client  # noqa: E402
from app.workflows.events import FORCE_REPROCESS_KEY, EventNames  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subject", default="Fw: test mail")
    parser.add_argument("--sender", default="kishan@hexalog.in")
    parser.add_argument("--code", action="append", default=[], help="e.g. utr:SHZ1234567:100000000001")
    parser.add_argument("--text", default="", help="text below the code lines, e.g. the forwarded mail")
    parser.add_argument("--text-file", type=Path, help="the body of a real mail, saved as text")
    parser.add_argument("--in-subject", action="store_true", help="put the codes in the subject, not the body")
    parser.add_argument("--attach", action="append", default=[], type=Path)
    args = parser.parse_args()

    settings = Settings.from_env()
    message_id = uuid.uuid4().hex
    folder = settings.storage_dir / "inbox" / message_id
    (folder / "attachments").mkdir(parents=True)
    for path in args.attach:
        shutil.copy(path, folder / "attachments" / path.name)
    lines = [f"{settings.inject_code_prefix}:{code}" for code in args.code]
    if args.in_subject:
        args.subject, lines = f"{' | '.join(lines)} {args.subject}", []
    message = {
        "id": message_id,
        "subject": args.subject,
        "sender": {"emailAddress": {"address": args.sender}},
        "body": {"content": "\n".join([*lines, "", args.text_file.read_text() if args.text_file else args.text])},
        "hasAttachments": bool(args.attach),
    }
    (folder / "message.json").write_text(json.dumps(message, indent=2))

    make_client(settings).send_sync(
        inngest.Event(name=EventNames.EMAIL_INBOUND, data={"message_id": message_id, FORCE_REPROCESS_KEY: ""})
    )
    print(f"Sent mail {message_id}. Watch it at http://localhost:8288 and in the job's History.")


if __name__ == "__main__":
    main()
