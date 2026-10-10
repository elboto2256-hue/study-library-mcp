"""Build a local text index from explicitly configured source directories."""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import sqlite3
import tempfile
from urllib.parse import unquote
from xml.etree import ElementTree as ET
import zipfile

from configuration import Config, load_config

TEXT_SUFFIXES = {".py", ".txt", ".md", ".bash", ".sh", ".yaml", ".yml", ".html", ".ipynb"}
SECRET_NAME = re.compile(r"(?:api[-_]?key|openai[-_]?key|secret|credential|\.env|^id_(?:rsa|dsa|ecdsa|ed25519))", re.I)
KEY_PATTERN = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b")


def clean(text: str) -> str:
    return KEY_PATTERN.sub("[REDACTED_API_KEY]", text.replace("\x00", ""))


def chunks(text: str, prefix: str = ""):
    """Preserve line locations and bound each passage to 4,000 characters."""
    lines = clean(text).splitlines()
    start = 0
    while start < len(lines):
        end, size = start, 0
        while end < len(lines) and end - start < 50:
            needed = len(lines[end]) + 1
            if end > start and size + needed > 3800:
                break
            size += needed
            end += 1
        part = "\n".join(lines[start:end])
        for offset in range(0, len(part), 4000):
            section = part[offset:offset + 4000]
            if section.strip():
                yield f"{prefix}lines {start + 1}-{end}; part {offset // 4000 + 1}", section
        start = end


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden and tag in {"p", "div", "h1", "h2", "h3", "li", "br", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".ipynb":
        notebook = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(notebook, dict) or notebook.get("nbformat") != 4 or not isinstance(notebook.get("cells"), list):
            raise ValueError("Expected a version-4 notebook with a cells list.")
        for number, cell in enumerate(notebook["cells"], 1):
            if not isinstance(cell, dict):
                raise ValueError(f"Invalid notebook cell {number}.")
            kind = cell.get("cell_type")
            if kind not in {"markdown", "code"}:
                continue
            source = cell.get("source", "")
            if isinstance(source, list) and all(isinstance(line, str) for line in source):
                source = "".join(source)
            elif not isinstance(source, str):
                raise ValueError(f"Invalid source in notebook cell {number}.")
            # Never execute source; ignore outputs, attachments, metadata, and raw cells.
            yield from chunks(source, f"Notebook cell {number} ({kind}); ")
    elif suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF: provide an accessible personal copy.")
        found = False
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            found = found or bool(text.strip())
            yield from chunks(text, f"PDF page {number}; ")
        if not found:
            raise ValueError("No extractable PDF text; OCR is needed for this scan.")
    elif suffix == ".epub":
        with zipfile.ZipFile(path) as archive:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = container.find(".//{*}rootfile").attrib["full-path"]
            package = ET.fromstring(archive.read(rootfile))
            manifest = {item.attrib["id"]: item.attrib["href"]
                        for item in package.findall(".//{*}manifest/{*}item")}
            for number, item in enumerate(package.findall(".//{*}spine/{*}itemref"), 1):
                href = unquote(manifest[item.attrib["idref"]].split("#")[0])
                member = posixpath.normpath(str(PurePosixPath(rootfile).parent / href))
                parser = PlainHTML()
                parser.feed(archive.read(member).decode("utf-8-sig"))
                yield from chunks("".join(parser.parts), f"EPUB section {number} ({member}); ")
    else:
        # HTML files in repositories are source code; EPUB markup is stripped above.
        yield from chunks(path.read_text(encoding="utf-8-sig"))


def candidates(config: Config):
    for root in config.sources:
        if root.path.is_symlink() or not root.path.is_dir():
            raise ValueError(f"Source directory missing or a symbolic link: {root.path}")
        for folder, directories, filenames in os.walk(root.path, followlinks=False):
            # Prune before traversal; never enter hidden, secret-named, or symlink folders.
            directories[:] = sorted(name for name in directories
                                    if not name.startswith(".") and not SECRET_NAME.search(name)
                                    and not (Path(folder) / name).is_symlink())
            for name in sorted(filenames):
                path = Path(folder) / name
                if name.startswith(".") or SECRET_NAME.search(name) or path.is_symlink():
                    continue
                if not path.is_file() or not path.resolve().is_relative_to(root.path):
                    continue
                allowed = TEXT_SUFFIXES | ({".pdf", ".epub"} if root.collection == "books" else set())
                if path.suffix.lower() not in allowed:
                    continue
                text_limit = (20 if path.suffix.lower() == ".ipynb" else 2) * 1024 * 1024
                if path.suffix.lower() not in {".pdf", ".epub"} and path.stat().st_size > text_limit:
                    continue
                yield root, path, path.relative_to(root.path).as_posix()


def build(config: Config) -> dict:
    database = config.database
    database.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix="index-", suffix=".sqlite3", dir=database.parent, delete=False)
    temporary = Path(handle.name)
    handle.close()
    conn = sqlite3.connect(temporary)
    errors = []
    try:
        conn.executescript("""
            CREATE TABLE sources(id TEXT PRIMARY KEY, title TEXT, path TEXT, collection TEXT, source_name TEXT);
            CREATE TABLE chunks(id TEXT PRIMARY KEY, source_id TEXT, location TEXT, text TEXT);
            CREATE INDEX chunks_source ON chunks(source_id);
            CREATE VIRTUAL TABLE passages USING fts5(text, title, tokenize='porter unicode61');
        """)
        for root, path, relative in candidates(config):
            source_id = hashlib.sha256(f"{root.collection}:{root.name}:{relative}".encode()).hexdigest()[:24]
            title = f"{root.collection}/{root.name}/{relative}"
            conn.execute("SAVEPOINT source")
            try:
                count = 0
                conn.execute("INSERT INTO sources VALUES (?,?,?,?,?)",
                             (source_id, title, str(path.resolve()), root.collection, root.name))
                for location, text in extract(path):
                    passage_id = hashlib.sha256(f"{source_id}:{location}:{text}".encode()).hexdigest()[:24]
                    cursor = conn.execute("INSERT INTO chunks VALUES (?,?,?,?)",
                                          (passage_id, source_id, location, text))
                    conn.execute("INSERT INTO passages(rowid,text,title) VALUES (?,?,?)",
                                 (cursor.lastrowid, text, title))
                    count += 1
                if not count:
                    conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
                conn.execute("RELEASE source")
            except Exception as exc:
                conn.execute("ROLLBACK TO source")
                conn.execute("RELEASE source")
                errors.append({"source": title, "error": str(exc)})
        conn.commit()
        stats = dict(conn.execute("SELECT collection, COUNT(*) FROM sources GROUP BY collection").fetchall())
        passages = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        conn.close()
        if errors:
            raise ValueError("Index not replaced because some files failed: " + json.dumps(errors))
        temporary.chmod(0o600)
        temporary.replace(database)
        return {"sources": stats, "passages": passages, "database": str(database)}
    finally:
        conn.close()
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="JSON config path (or STUDY_LIBRARY_CONFIG; default config.json)")
    args = parser.parse_args()
    print(json.dumps(build(load_config(args.config)), indent=2))
