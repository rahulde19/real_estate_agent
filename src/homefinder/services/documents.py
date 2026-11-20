"""SQLite store for search results and their saved extractions (one row per source URL).

All SQL lives here so a later move to Postgres only changes this file.
"""
import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url        TEXT NOT NULL UNIQUE,
    title             TEXT,
    agent_source      TEXT NOT NULL,
    first_seen_at     TEXT NOT NULL,
    retrieved_at      TEXT NOT NULL,
    raw_text          TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    extraction_json   TEXT,
    extraction_status TEXT NOT NULL DEFAULT 'pending',  -- pending | ok | failed | skipped (prefilter; reason in `failure`)
    failure           TEXT,
    prompt_version    TEXT,
    model_id          TEXT,
    extracted_at      TEXT
)
"""


@dataclass
class Document:
    id: int
    source_url: str
    title: str | None
    agent_source: str
    first_seen_at: str
    retrieved_at: str
    raw_text: str
    content_hash: str
    extraction_json: str | None
    extraction_status: str
    failure: str | None
    prompt_version: str | None
    model_id: str | None
    extracted_at: str | None

    def answer_json(self) -> dict:
        """The saved page answer: {"source_type", "listings", "evidence"}. A bare list (older rows) is the listings."""
        data = json.loads(self.extraction_json) if self.extraction_json else {}
        return {"listings": data} if isinstance(data, list) else data

    def listings_json(self) -> list[dict]:
        return self.answer_json().get("listings", [])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DocumentRepo:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)  # graph nodes may run on worker threads
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute(_SCHEMA)

    def get(self, source_url: str) -> Document | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM documents WHERE source_url = ?", (source_url,)).fetchone()
        return Document(**dict(row)) if row else None

    def all(self) -> list[Document]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM documents ORDER BY id").fetchall()
        return [Document(**dict(r)) for r in rows]

    def save_result(self, result: dict, agent_source: str, now: str | None = None) -> Document:
        """Insert a search result, or refresh it. Unchanged text keeps its saved extraction;
        changed text replaces it and resets the extraction to pending."""
        now = now or _now()
        url, text, h = result["url"], result.get("content", ""), _hash(result.get("content", ""))
        existing = self.get(url)
        with self._lock, self._db:
            if existing is None:
                self._db.execute(
                    "INSERT INTO documents (source_url, title, agent_source, first_seen_at, retrieved_at, raw_text, content_hash)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (url, result.get("title", ""), agent_source, now, now, text, h))
            elif existing.content_hash == h:
                self._db.execute("UPDATE documents SET retrieved_at = ?, agent_source = ? WHERE source_url = ?",
                                 (now, agent_source, url))
            else:
                self._db.execute(
                    "UPDATE documents SET title = ?, agent_source = ?, retrieved_at = ?, raw_text = ?, content_hash = ?,"
                    " extraction_json = NULL, extraction_status = 'pending', failure = NULL, prompt_version = NULL,"
                    " model_id = NULL, extracted_at = NULL WHERE source_url = ?",
                    (result.get("title", ""), agent_source, now, text, h, url))
        return self.get(url)

    @staticmethod
    def needs_extraction(doc: Document, prompt_version: str, model_id: str) -> bool:
        """Pending, or last extracted (or failed) under a different prompt version or model."""
        if doc.extraction_status in ("pending", "skipped"):  # skipped pages are re-checked by the filter every run
            return True
        return (doc.prompt_version, doc.model_id) != (prompt_version, model_id)

    def save_extraction(self, source_url: str, answer: dict | list, prompt_version: str, model_id: str) -> None:
        """`answer` is the page answer dict (or a bare list of listings)."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE documents SET extraction_json = ?, extraction_status = 'ok', failure = NULL, prompt_version = ?,"
                " model_id = ?, extracted_at = ? WHERE source_url = ?",
                (json.dumps(answer), prompt_version, model_id, _now(), source_url))

    def save_skipped(self, source_url: str, reason: str) -> None:
        """Mark a page the prefilter rejected. Its text stays saved (later phases use review pages)."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE documents SET extraction_json = NULL, extraction_status = 'skipped', failure = ?, prompt_version = NULL,"
                " model_id = NULL, extracted_at = NULL WHERE source_url = ?", (reason, source_url))

    def save_failure(self, source_url: str, error: str, prompt_version: str, model_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE documents SET extraction_json = NULL, extraction_status = 'failed', failure = ?, prompt_version = ?,"
                " model_id = ?, extracted_at = ? WHERE source_url = ?",
                (error, prompt_version, model_id, _now(), source_url))
