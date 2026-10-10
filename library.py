"""Read-only keyword retrieval from an already-built local SQLite index."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import re
import sqlite3

COLLECTIONS = {"all", "repo", "notes", "books"}


class Library:
    def __init__(self, database: str | Path):
        self.database = Path(database).resolve()

    def connect(self):
        if not self.database.is_file():
            raise ValueError("Library index missing. Run build_index.py first.")
        conn = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    @staticmethod
    def validate_collection(collection: str):
        if collection not in COLLECTIONS:
            raise ValueError("collection must be all, repo, notes, or books")

    def list_sources(self, collection: str = "all", limit: int = 100, offset: int = 0) -> dict:
        self.validate_collection(collection)
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError("limit must be 1–200; offset must be a nonnegative integer")
        with closing(self.connect()) as conn:
            total = conn.execute("SELECT COUNT(*) FROM sources WHERE (?='all' OR collection=?)",
                                 (collection, collection)).fetchone()[0]
            rows = conn.execute(
                "SELECT s.id, s.title, s.path, s.collection, s.source_name, COUNT(c.id) AS passages "
                "FROM sources s JOIN chunks c ON c.source_id=s.id "
                "WHERE (?='all' OR s.collection=?) GROUP BY s.id ORDER BY s.title LIMIT ? OFFSET ?",
                (collection, collection, limit, offset),
            ).fetchall()
        return {"sources": [dict(row) for row in rows], "total": total,
                "next_offset": offset + len(rows) if offset + len(rows) < total else None,
                "coverage": "repo = code/examples; notes = study synthesis; books = user-provided text. "
                            "Collections are user assigned; verify titles and contents."}

    def search(self, query: str, limit: int = 6, collection: str = "all") -> dict:
        self.validate_collection(collection)
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError("limit must be between 1 and 12")
        if not isinstance(query, str) or len(query) > 500:
            raise ValueError("query must be a string of at most 500 characters")
        terms = list(dict.fromkeys(re.findall(r"\w+", query, re.UNICODE)))[:16]
        if not terms:
            return {"results": [], "search_mode": "keyword", "note": "Enter some keywords."}
        terms = ['"' + term + '"' for term in terms]
        sql = (
            "SELECT c.id, s.title, s.path, s.collection, s.source_name, c.location, "
            "snippet(passages, 0, '[', ']', ' ... ', 50) AS excerpt "
            "FROM passages JOIN chunks c ON c.rowid=passages.rowid "
            "JOIN sources s ON s.id=c.source_id "
            "WHERE passages MATCH ? AND (?='all' OR s.collection=?) "
            "ORDER BY bm25(passages) LIMIT ?"
        )
        with closing(self.connect()) as conn:
            mode = "all_keywords"
            rows = conn.execute(sql, (" AND ".join(terms), collection, collection, limit)).fetchall()
            if not rows:
                mode = "any_keyword"
                rows = conn.execute(sql, (" OR ".join(terms), collection, collection, limit)).fetchall()
        return {"results": [dict(row) for row in rows], "search_mode": mode,
                "note": "Keyword search; fetch passage IDs for evidence. Study notes are not book quotations."}

    def fetch(self, passage_id: str) -> dict:
        if not isinstance(passage_id, str) or not re.fullmatch(r"[0-9a-f]{24}", passage_id):
            raise ValueError("Use a passage ID returned by search.")
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT c.id, s.title, s.path, s.collection, s.source_name, c.location, c.text "
                "FROM chunks c JOIN sources s ON s.id=c.source_id WHERE c.id=?", (passage_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Unknown passage ID. Re-run search if the index was rebuilt.")
        return dict(row)
