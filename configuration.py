"""Load explicit local source roots; resolve relative paths beside the config."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

COLLECTIONS = {"repo", "notes", "books"}


@dataclass(frozen=True)
class SourceRoot:
    name: str
    collection: str
    path: Path


@dataclass(frozen=True)
class Config:
    database: Path
    sources: tuple[SourceRoot, ...]


def load_config(filename: str | Path | None = None) -> Config:
    path = Path(filename or os.environ.get("STUDY_LIBRARY_CONFIG", "config.json")).expanduser().resolve()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Config missing: {path}. Copy and edit config.example.json first.") from exc
    if not isinstance(document, dict):
        raise ValueError("Config must be a JSON object.")
    database = document.get("database", "data/library.sqlite3")
    entries = document.get("sources", [])
    if not isinstance(database, str) or not database.strip() or not isinstance(entries, list):
        raise ValueError("database must be a path string; sources must be a list.")

    def local_path(value: str) -> Path:
        result = Path(value).expanduser()
        return (path.parent / result).absolute() if not result.is_absolute() else result.absolute()

    roots, names = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each source must be an object with name, collection, and path.")
        name, collection, folder = (entry.get(key) for key in ("name", "collection", "path"))
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
            raise ValueError("Source names must be 1–64 letters, digits, dots, underscores, or hyphens.")
        if name in names:
            raise ValueError(f"Duplicate source name: {name}")
        if not isinstance(collection, str) or collection not in COLLECTIONS:
            raise ValueError("Source collection must be repo, notes, or books.")
        if not isinstance(folder, str) or not folder.strip():
            raise ValueError("Each source needs an explicit directory path.")
        root = local_path(folder)
        if root.is_symlink() or not root.is_dir():
            raise ValueError(f"Source directory missing or a symbolic link: {root}")
        roots.append(SourceRoot(name, collection, root.resolve()))
        names.add(name)
    return Config(local_path(database).resolve(), tuple(roots))
