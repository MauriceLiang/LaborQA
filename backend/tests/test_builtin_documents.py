import io
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest
from fastapi import BackgroundTasks

from app.api.contracts import get_builtin_document_status, import_builtin_documents
from app.core.config import Settings, settings
from app.core.database import initialize_database
from app.repositories.document_repository import DocumentRepository
from app.schemas.contracts import EmbeddingSignature
from app.services.builtin_documents import list_builtin_documents, sha256_file
from app.services.document_service import DocumentService
from app.services.file_storage import FileStorage
from app.services.vector_store import VectorStoreService

SCHEMA_PATH = Path(__file__).parents[1] / "app" / "core" / "schema.sql"


class _Parser:
    @staticmethod
    def parse(path: str, file_type: str) -> str:
        return Path(path).read_text(encoding="utf-8")


class _EmbeddingService:
    provider = SimpleNamespace(
        name="local",
        model="builtin-test-embedding",
        normalize_embeddings=True,
        dimension=2,
    )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def signature(self, embedding_dimension: int | None = None) -> EmbeddingSignature:
        return EmbeddingSignature(
            embedding_provider="local",
            embedding_model="builtin-test-embedding",
            embedding_dimension=embedding_dimension or 2,
            normalize_embeddings=True,
        )


def _service(
    tmp_path: Path, builtin_dir: Path
) -> tuple[DocumentService, DocumentRepository]:
    database_path = tmp_path / "documents.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    config = Settings(
        _env_file=None,
        database_url=f"sqlite:///{database_path}",
        upload_dir=tmp_path / "uploads",
        faiss_dir=tmp_path / "faiss",
        builtin_document_dir=builtin_dir,
        chunk_size=600,
        chunk_overlap=0,
    )
    repository = DocumentRepository(config.database_path)
    embedding_service = _EmbeddingService()
    service = DocumentService(
        config,
        repository=repository,
        file_storage=FileStorage(config.upload_path),
        parser=_Parser,
        embedding_service=embedding_service,  # type: ignore[arg-type]
        vector_store=VectorStoreService(
            config,
            embedding_service=embedding_service,
            index_dir=config.faiss_path / "production",
        ),
    )
    return service, repository


def test_builtin_scanner_uses_stable_keys_and_sha256(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"ignored")
    config = Settings(_env_file=None, builtin_document_dir=tmp_path)

    specs = list_builtin_documents(config)

    assert [spec.source_key for spec in specs] == ["builtin:a.txt", "builtin:b.md"]
    assert specs[0].source_hash == sha256_file(tmp_path / "a.txt")


def test_builtin_api_reports_status_and_schedules_one_batch(tmp_path: Path) -> None:
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    (builtin_dir / "law.md").write_text("系统资料", encoding="utf-8")
    service, _ = _service(tmp_path, builtin_dir)

    status_response = get_builtin_document_status(service)
    assert status_response.data.total == 1
    assert status_response.data.missing == 1

    background_tasks = BackgroundTasks()
    accepted_response = import_builtin_documents(background_tasks, service)

    assert accepted_response.data.total == 1
    assert accepted_response.data.scheduled == 1
    assert accepted_response.data.processing == 0
    assert accepted_response.data.tracked_document_ids
    assert len(background_tasks.tasks) == 1


def test_builtin_lifecycle_imports_in_order_and_is_idempotent(tmp_path: Path) -> None:
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    (builtin_dir / "a.md").write_text("第一份资料", encoding="utf-8")
    (builtin_dir / "b.md").write_text("第二份资料", encoding="utf-8")
    service, repository = _service(tmp_path, builtin_dir)

    initial = service.get_builtin_status()
    assert (initial["total"], initial["missing"], initial["all_imported"]) == (
        2,
        2,
        False,
    )

    plan = service.prepare_builtin_import()
    assert plan.total == 2
    assert plan.already_imported == 0
    assert len(plan.scheduled_ids) == 2
    assert plan.processing_ids == ()
    service.import_builtin_batch(list(plan.scheduled_ids))

    completed = service.get_builtin_status()
    assert (completed["success"], completed["missing"], completed["all_imported"]) == (
        2,
        0,
        True,
    )
    documents, total = repository.list_documents(size=10)
    assert total == 2
    assert [document["source_key"] for document in reversed(documents)] == [
        "builtin:a.md",
        "builtin:b.md",
    ]

    second_plan = service.prepare_builtin_import()
    assert second_plan.already_imported == 2
    assert second_plan.scheduled_ids == ()
    assert second_plan.processing_ids == ()


def test_legacy_upload_with_matching_hash_is_claimed(tmp_path: Path) -> None:
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    content = "历史手动上传的系统资料"
    (builtin_dir / "劳动法.md").write_text(content, encoding="utf-8")
    service, repository = _service(tmp_path, builtin_dir)

    uploaded = service.upload(
        io.BytesIO(content.encode()), "劳动法.md", "text/markdown"
    )
    repository.update_document(uploaded["id"], status="SUCCESS", chunk_count=0)

    status = service.get_builtin_status()

    assert status["success"] == 1
    assert status["items"][0]["document_id"] == uploaded["id"]
    claimed = repository.get_document(uploaded["id"])
    assert claimed["source_type"] == "BUILTIN"
    assert claimed["source_key"] == "builtin:劳动法.md"


def test_same_name_with_different_content_is_not_claimed(tmp_path: Path) -> None:
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    (builtin_dir / "劳动法.md").write_text("系统版本", encoding="utf-8")
    service, repository = _service(tmp_path, builtin_dir)

    uploaded = service.upload(
        io.BytesIO("用户版本".encode()), "劳动法.md", "text/markdown"
    )
    repository.update_document(uploaded["id"], status="SUCCESS", chunk_count=0)

    assert service.get_builtin_status()["missing"] == 1
    plan = service.prepare_builtin_import()

    assert len(plan.scheduled_ids) == 1
    assert len(repository.list_documents_by_file_name("劳动法.md")) == 2
    user_document = repository.get_document(uploaded["id"])
    assert user_document["source_type"] == "UPLOAD"


def test_outdated_refresh_preserves_document_id_and_removes_citations(
    tmp_path: Path,
) -> None:
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    builtin_path = builtin_dir / "law.md"
    builtin_path.write_text("旧版本资料", encoding="utf-8")
    service, repository = _service(tmp_path, builtin_dir)

    first_plan = service.prepare_builtin_import()
    service.import_builtin_batch(list(first_plan.scheduled_ids))
    document = repository.get_document_by_source_key("builtin:law.md")
    chunks, _ = repository.list_chunks(document["id"])
    with repository._connection() as connection:
        session_id = "builtin-refresh-session"
        connection.execute(
            "INSERT INTO session (id, title) VALUES (?, ?)", (session_id, "刷新")
        )
        cursor = connection.execute(
            "INSERT INTO message (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, "assistant", "引用"),
        )
        connection.execute(
            """
            INSERT INTO citation (message_id, chunk_id, score, retrieval_score, rank_no)
            VALUES (?, ?, ?, ?, ?)
            """,
            (cursor.lastrowid, chunks[0]["id"], 0.9, 0.8, 1),
        )

    builtin_path.write_text("新版本资料", encoding="utf-8")
    assert service.get_builtin_status()["outdated"] == 1

    refresh_plan = service.prepare_builtin_import()
    assert refresh_plan.scheduled_ids == (document["id"],)
    assert repository.list_chunks(document["id"]) == ([], 0)
    with repository._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM citation").fetchone()[0] == 0

    service.import_builtin_batch(list(refresh_plan.scheduled_ids))
    refreshed = repository.get_document(document["id"])
    chunks, total = repository.list_chunks(document["id"])
    assert refreshed["status"] == "SUCCESS"
    assert total == 1
    assert chunks[0]["content"] == "新版本资料"


def test_builtin_batch_continues_after_an_unexpected_failure(tmp_path: Path) -> None:
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    service, _ = _service(tmp_path, builtin_dir)

    with patch.object(
        service,
        "import_document",
        side_effect=[None, RuntimeError("one failure"), None],
    ) as importer:
        service.import_builtin_batch([1, 2, 3])

    assert importer.call_args_list == [call(1), call(2), call(3)]


def test_old_document_schema_migrates_source_columns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "old.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE document (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name VARCHAR(255) NOT NULL,
                file_type VARCHAR(20) NOT NULL
                    CHECK (file_type IN ('pdf', 'doc', 'docx', 'txt')),
                file_path VARCHAR(500) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'PROCESSING'
                    CHECK (status IN ('PROCESSING', 'SUCCESS', 'FAILED')),
                chunk_count INTEGER NOT NULL DEFAULT 0,
                error_message VARCHAR(500),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO document (file_name, file_type, file_path)
            VALUES ('old.txt', 'txt', '/tmp/old.txt');
            """
        )

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{database_path}")
    initialize_database()

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(document)")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(document)")}
        row = connection.execute(
            "SELECT source_type, source_key, source_hash FROM document WHERE id = 1"
        ).fetchone()

    assert {"source_type", "source_key", "source_hash"} <= columns
    assert "idx_document_source_key" in indexes
    assert row == ("UPLOAD", None, None)
