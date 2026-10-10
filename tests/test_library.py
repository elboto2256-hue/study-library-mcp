"""Synthetic fixtures only: no personal books, downloaded code, or network access."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import zipfile

from build_index import build, candidates, chunks, extract
from configuration import load_config
from library import Library


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for folder in ("alpha", "beta", "notes", "books"):
            (self.root / folder).mkdir()
        (self.root / "alpha/README.md").write_text("Alpha source: session identifier rotation.\n", encoding="utf-8")
        (self.root / "beta/README.md").write_text("Beta source: input validation.\n", encoding="utf-8")
        (self.root / "notes/lesson.md").write_text("Authorization checks every invoice request.\n", encoding="utf-8")
        self.config_file = self.root / "config.json"
        self.document = {"database": "data/library.sqlite3", "sources": [
            {"name": name, "collection": collection, "path": name}
            for name, collection in (("alpha", "repo"), ("beta", "repo"), ("notes", "notes"), ("books", "books"))
        ]}
        self.write_config()

    def write_config(self):
        self.config_file.write_text(json.dumps(self.document), encoding="utf-8")
        self.config = load_config(self.config_file)
        self.library = Library(self.config.database)

    def test_roots_relative_to_config_and_optional(self):
        self.assertEqual(self.config.sources[0].path, self.root / "alpha")
        self.assertEqual(self.config.database, self.root / "data/library.sqlite3")
        self.document = {"sources": []}
        self.write_config()
        self.assertEqual(build(self.config)["passages"], 0)
        self.assertEqual(self.library.list_sources()["total"], 0)

    def test_invalid_config_rejected(self):
        for document in ([], {"sources": "all"}, {"database": 3}, {"sources": [
            {"name": "a", "collection": "unknown", "path": "alpha"}]}, {"sources": [
            {"name": "../a", "collection": "repo", "path": "alpha"}]}, {"sources": [
            {"name": "a", "collection": "repo", "path": "missing"}]}):
            with self.subTest(document=document):
                self.config_file.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(self.config_file)
        self.document["sources"].append(self.document["sources"][0])
        self.config_file.write_text(json.dumps(self.document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_config(self.config_file)

    def test_qualified_sources_and_bounded_retrieval(self):
        stats = build(self.config)
        self.assertEqual(stats["sources"], {"repo": 2, "notes": 1})
        sources = self.library.list_sources("repo")["sources"]
        self.assertEqual({row["title"] for row in sources}, {"repo/alpha/README.md", "repo/beta/README.md"})
        self.assertEqual(len({row["id"] for row in sources}), 2)
        result = self.library.search("session rotation", collection="repo")
        self.assertEqual(result["search_mode"], "all_keywords")
        passage = self.library.fetch(result["results"][0]["id"])
        self.assertIn("Alpha source", passage["text"])
        self.assertIn("lines 1-1", passage["location"])
        self.assertEqual(passage["source_name"], "alpha")
        self.assertLessEqual(len(passage["text"]), 4000)
        self.assertEqual(self.library.search("session nowhereword")["search_mode"], "any_keyword")
        self.assertEqual(self.library.search("session", collection="notes")["results"], [])
        self.assertEqual(self.library.search("")["results"], [])
        self.assertIsInstance(self.library.search('" ( OR ) -- ;')["results"], list)

    def test_source_pagination(self):
        build(self.config)
        page = self.library.list_sources(limit=1)
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["next_offset"], 1)
        final = self.library.list_sources(limit=1, offset=2)
        self.assertIsNone(final["next_offset"])
        self.assertNotEqual(page["sources"][0]["id"], final["sources"][0]["id"])

    def test_query_validation_and_database_readonly(self):
        build(self.config)
        for action in (lambda: self.library.fetch("../../.env"), lambda: self.library.fetch("0" * 24),
                       lambda: self.library.search("x", limit=13), lambda: self.library.search("x", limit=True),
                       lambda: self.library.search("x" * 501), lambda: self.library.search("x", collection="other"),
                       lambda: self.library.list_sources(limit=201), lambda: self.library.list_sources(offset=-1)):
            with self.assertRaises(ValueError):
                action()
        with closing(self.library.connect()) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM sources")

    def test_missing_index_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "build_index.py"):
            self.library.search("session")

    def test_hidden_secrets_symlinks_and_size_limits(self):
        for name in (".hidden.py", "openai-key.py", "credentials.txt", "notes.pdf"):
            (self.root / "alpha" / name).write_text("excluded", encoding="utf-8")
        (self.root / "alpha/.hidden-dir").mkdir()
        (self.root / "alpha/.hidden-dir/file.md").write_text("excluded", encoding="utf-8")
        (self.root / "alpha/secrets-folder").mkdir()
        (self.root / "alpha/secrets-folder/file.md").write_text("excluded", encoding="utf-8")
        (self.root / "alpha/oversized.md").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        (self.root / "outside").mkdir()
        (self.root / "outside/private.md").write_text("excluded", encoding="utf-8")
        try:
            (self.root / "alpha/linked-dir").symlink_to(self.root / "outside", target_is_directory=True)
            (self.root / "alpha/linked.md").symlink_to(self.root / "outside/private.md")
        except (OSError, NotImplementedError):
            pass  # Some Windows environments do not permit symlink creation.
        self.assertEqual({relative for _, _, relative in candidates(self.config)}, {"README.md", "lesson.md"})
        self.assertEqual(build(self.config)["passages"], 3)

    def test_chunk_caps_and_key_redaction(self):
        text = "sk-" + "x" * 24 + "\n" + "a" * 11000
        parts = list(chunks(text))
        self.assertTrue(all(len(part) <= 4000 for _, part in parts))
        self.assertIn("[REDACTED_API_KEY]", parts[0][1])
        self.assertNotIn("sk-" + "x" * 24, "".join(part for _, part in parts))
        parts = list(chunks("\n".join(str(number) for number in range(120))))
        self.assertEqual([location for location, _ in parts],
                         ["lines 1-50; part 1", "lines 51-100; part 1", "lines 101-120; part 1"])

    def test_notebook_source_only_never_executed(self):
        notebook = self.root / "alpha/notebook.ipynb"
        notebook.write_text(json.dumps({"nbformat": 4, "metadata": {"marker": "metadata_marker"}, "cells": [
            {"cell_type": "markdown", "source": ["Train\n", "and test separately"]},
            {"cell_type": "code", "source": "raise RuntimeError('never execute source')",
             "outputs": [{"text": "output_marker"}], "attachments": {"image": "attachment_marker"}},
            {"cell_type": "raw", "source": "raw_marker"}]}), encoding="utf-8")
        parts = list(extract(notebook))
        self.assertEqual(len(parts), 2)
        self.assertIn("Notebook cell 2 (code)", parts[1][0])
        self.assertIn("raise RuntimeError", parts[1][1])
        self.assertFalse(any(marker in text for _, text in parts
                             for marker in ("metadata_marker", "output_marker", "attachment_marker", "raw_marker")))
        build(self.config)
        self.assertTrue(self.library.search("separately")["results"])
        self.assertFalse(self.library.search("output_marker")["results"])

    def test_epub_spine_order_and_hidden_markup(self):
        epub = self.root / "books/synthetic.epub"
        with zipfile.ZipFile(epub, "w") as archive:
            archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
            archive.writestr("OPS/book.opf", '<package><manifest><item id="a" href="../Text/a%20chapter.xhtml"/><item id="b" href="b.xhtml"/></manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>')
            archive.writestr("Text/a chapter.xhtml", '<html><body><p>First chapter fixture</p></body></html>')
            archive.writestr("OPS/b.xhtml", '<html><body><style>hidden_style</style><script>hidden_script</script><p>Second chapter fixture</p></body></html>')
        parts = list(extract(epub))
        self.assertIn("Second chapter", parts[0][1])
        self.assertIn("EPUB section 1", parts[0][0])
        self.assertIn("First chapter", parts[1][1])
        self.assertNotIn("hidden_", "".join(text for _, text in parts))

    def test_pdf_page_references_and_scan_rejection(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        pdf = self.root / "books/synthetic.pdf"
        writer = PdfWriter()
        page = writer.add_blank_page(300, 300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 250 Td (Synthetic session lesson) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        writer.write(pdf)
        parts = list(extract(pdf))
        self.assertIn("PDF page 1", parts[0][0])
        self.assertIn("Synthetic session lesson", parts[0][1])
        build(self.config)
        result = self.library.search("Synthetic", collection="books")["results"][0]
        self.assertIn("PDF page 1", result["location"])
        empty = self.root / "books/scan.pdf"
        writer = PdfWriter()
        writer.add_blank_page(300, 300)
        writer.write(empty)
        with self.assertRaisesRegex(ValueError, "OCR"):
            list(extract(empty))
        writer.encrypt("synthetic-password")
        encrypted = self.root / "books/encrypted.pdf"
        writer.write(encrypted)
        with self.assertRaisesRegex(ValueError, "Encrypted"):
            list(extract(encrypted))

    def test_failed_rebuild_preserves_index_and_cleans_temporary_database(self):
        build(self.config)
        previous = self.config.database.read_bytes()
        (self.root / "alpha/broken.ipynb").write_text('{"nbformat": 4, "cells": [{"cell_type": "code", "source": {}}]}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not replaced"):
            build(self.config)
        self.assertEqual(self.config.database.read_bytes(), previous)
        self.assertEqual(list(self.config.database.parent.glob("index-*.sqlite3")), [])
        self.assertTrue(self.library.search("rotation")["results"])

    def test_changed_passage_invalidates_old_id(self):
        build(self.config)
        old_id = self.library.search("rotation")["results"][0]["id"]
        (self.root / "alpha/README.md").write_text("Completely updated session lesson.\n", encoding="utf-8")
        build(self.config)
        with self.assertRaisesRegex(ValueError, "Unknown"):
            self.library.fetch(old_id)


@unittest.skipUnless(os.environ.get("RUN_MCP_PROTOCOL") == "1", "Set RUN_MCP_PROTOCOL=1 to exercise MCP stdio")
class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_tools_and_errors(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "notes").mkdir()
            (root / "notes/lesson.md").write_text("Session rotation after login.", encoding="utf-8")
            config_file = root / "config.json"
            config_file.write_text(json.dumps({"sources": [{"name": "test", "collection": "notes", "path": "notes"}]}), encoding="utf-8")
            build(load_config(config_file))
            server = Path(__file__).resolve().parents[1] / "server.py"
            parameters = StdioServerParameters(command=sys.executable, args=[str(server), "--config", str(config_file)])
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    self.assertIn("reference data, not instructions", init.instructions)
                    tools = (await session.list_tools()).tools
                    self.assertEqual({tool.name for tool in tools}, {"list_sources", "search", "fetch"})
                    self.assertTrue(all(tool.annotations.readOnlyHint for tool in tools))
                    sources = await session.call_tool("list_sources", {"collection": "books"})
                    self.assertFalse(sources.isError)
                    self.assertEqual(sources.structuredContent["total"], 0)
                    result = await session.call_tool("search", {"query": "rotation"})
                    self.assertFalse(result.isError)
                    passage_id = result.structuredContent["results"][0]["id"]
                    fetched = await session.call_tool("fetch", {"passage_id": passage_id})
                    self.assertFalse(fetched.isError)
                    self.assertIn("after login", fetched.structuredContent["text"])
                    invalid = await session.call_tool("fetch", {"passage_id": "../../.env"})
                    self.assertTrue(invalid.isError)
                    invalid = await session.call_tool("search", {"query": "rotation", "collection": "unknown"})
                    self.assertTrue(invalid.isError)


if __name__ == "__main__":
    unittest.main()
