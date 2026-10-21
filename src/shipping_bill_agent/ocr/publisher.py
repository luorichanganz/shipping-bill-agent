from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol


class FilePublisher(Protocol):
    def publish(self, data: bytes, file_name: str, mime_type: str | None) -> str: ...


class StaticDirectoryPublisher:
    """Publish files into a directory served by an external HTTP service."""

    def __init__(self, directory: str | Path, base_url: str):
        self.directory = Path(directory)
        self.base_url = base_url.rstrip("/")

    def publish(self, data: bytes, file_name: str, mime_type: str | None) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        suffix = Path(file_name).suffix.lower() or ".bin"
        digest = hashlib.sha256(data).hexdigest()
        destination = self.directory / f"{digest}{suffix}"
        if not destination.exists():
            destination.write_bytes(data)
        return f"{self.base_url}/{destination.name}"
