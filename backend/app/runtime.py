"""What the API and the Inngest workflows share, built once by create_app."""
from __future__ import annotations

from dataclasses import dataclass

import inngest
from sqlalchemy.orm import sessionmaker

from .config import Settings
from .folder import Folder


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    folder: Folder
    sessionmaker: sessionmaker
    inngest: inngest.Inngest
