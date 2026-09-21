"""Discovery and identity helpers for repository-provided documents."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.core.config import Settings, settings

SUPPORTED_BUILTIN_SUFFIXES = frozenset({".pdf", ".doc", ".docx", ".md", ".txt"})


@dataclass(frozen=True)
class BuiltinDocumentSpec:
    source_key: str
    file_name: str
    file_type: str
    source_path: Path
    source_hash: str


@dataclass(frozen=True)
class BuiltinImportPlan:
    total: int
    already_imported: int
    scheduled_ids: tuple[int, ...]
    processing_ids: tuple[int, ...]

    @property
    def tracked_document_ids(self) -> list[int]:
        return [*self.scheduled_ids, *self.processing_ids]

    @property
    def all_imported(self) -> bool:
        return self.total > 0 and self.already_imported == self.total


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def list_builtin_documents(
    config: Settings = settings,
) -> list[BuiltinDocumentSpec]:
    root = config.builtin_document_path
    if not root.is_dir():
        return []

    paths = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_BUILTIN_SUFFIXES
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    specs: list[BuiltinDocumentSpec] = []
    for path in paths:
        relative_path = path.relative_to(root).as_posix()
        specs.append(
            BuiltinDocumentSpec(
                source_key=f"builtin:{relative_path}",
                file_name=path.name,
                file_type=path.suffix.lower().removeprefix("."),
                source_path=path,
                source_hash=sha256_file(path),
            )
        )
    return specs
