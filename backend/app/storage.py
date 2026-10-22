from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.config import settings


class DocumentStorage(Protocol):
    def save(self, content: bytes, suffix: str) -> str: ...
    def read(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalDocumentStorage:
    def __init__(self, root: str = settings.storage_dir):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, content: bytes, suffix: str) -> str:
        key = f"{uuid4().hex}{suffix}"
        (self.root / key).write_bytes(content)
        return key

    def read(self, key: str) -> bytes:
        path = (self.root / key).resolve()
        if path.parent != self.root:
            raise ValueError("Invalid document key")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = (self.root / key).resolve()
        if path.parent == self.root:
            path.unlink(missing_ok=True)


storage = LocalDocumentStorage()
