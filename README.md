# Study Library MCP

A small, local, read-only MCP server for studying your own notes, companion code,
and accessible PDF or EPUB books. Search returns source files and locations;
fetch returns one passage of at most 4,000 characters. It uses SQLite keyword
search, without embeddings, model API keys, or a hosted database.

The repository includes only the toolkit and one original sample note. It does
not include commercial books, third-party repositories, or a user's private index.
The MIT license covers this toolkit and sample, not the material you choose to index.

## Set up

Requires Python 3.10+ with SQLite FTS5 support. These commands are for macOS/Linux;
on Windows use `.venv\Scripts\python.exe` in place of `.venv/bin/python`.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config.example.json config.json
.venv/bin/python build_index.py --config config.json
```

The example indexes the included sample note. Edit `config.json` to add only the
directories you want indexed, then rebuild. Paths are relative to the config
file, or may be absolute; `~` is supported. Source names must be unique. Any of
the collections may be omitted, and `"sources": []` produces an empty library.

```json
{
  "database": "data/library.sqlite3",
  "sources": [
    {"name": "my-notes", "collection": "notes", "path": "notes"},
    {"name": "book-examples", "collection": "repo", "path": "../book-examples"},
    {"name": "my-books", "collection": "books", "path": "books"}
  ]
}
```

Each configured directory must exist. Names qualify source IDs and titles, so
two repositories containing `README.md` remain distinct. Adding, editing, or
removing source files requires a rebuild; the server never crawls your computer
or fetches websites. A failed extraction leaves the previous index intact.

## Connect an MCP client

Run from this repository to register with the Codex CLI:

```sh
codex mcp add study-library -- "$(pwd)/.venv/bin/python" "$(pwd)/server.py" --config "$(pwd)/config.json"
codex mcp get study-library
```

For other clients, configure a stdio server with the absolute virtual-environment
Python path as the command, and absolute `server.py`, `--config`, and the absolute
config path as arguments. `STUDY_LIBRARY_CONFIG` can also supply the config path.
The indexer and server otherwise default to `config.json` in the working directory.
Reconnect the server if your client does not discover the tools immediately.

Try: “List the indexed sources. Explain session rotation using the available
notes, cite the file and location, and ask me a practice question.”

To remove the Codex registration: `codex mcp remove study-library`.

## Tools and boundaries

| Tool | Result |
| --- | --- |
| `list_sources(collection="all", limit=100, offset=0)` | Paginated coverage, up to 200 sources per call |
| `search(query, limit=6, collection="all")` | Up to 12 keyword matches, excerpts, locations, and passage IDs |
| `fetch(passage_id)` | One indexed passage, up to 4,000 characters |

Collections are `repo`, `notes`, and `books`. They describe your intended sources;
the server does not establish whether a file is a complete book. Notes must not
be presented as quotations from a book. Returned content is reference data and
must not be treated as instructions to the assistant.

- Text/code formats: `.py`, `.txt`, `.md`, `.bash`, `.sh`, `.yaml`, `.yml`, `.html`.
  Files are capped at 2 MiB; HTML is indexed as source text.
- Version-4 notebooks: source from code and Markdown cells only, up to 20 MiB
  per file. No code execution, outputs, attachments, metadata, or raw cells.
- The `books` collection additionally accepts PDFs and EPUBs. PDF references
  use physical PDF page numbers; EPUB references use spine sections. PDF/EPUB
  files have no size cap, so add appropriately sized trusted files.
- Encrypted PDFs are rejected; scans with no text need OCR first. Text extraction
  does not preserve visual layout, figures, or complex tables. No OCR is included.
- Hidden paths, symbolic links, and obvious secret filenames are skipped.
  OpenAI-key-shaped strings are redacted. This is not a general secret scanner;
  review your source roots before indexing.

The MCP tools only read the index and cannot read arbitrary paths, rebuild the
index, execute examples, or issue security commands. The server uses stdio and
does not start an HTTP listener. Search is lexical, with stemming and an
all-keywords/any-keyword fallback; it is not semantic search.

## Privacy and sharing

Indexing happens locally with no model or network request. The index contains
extracted source text and absolute file paths; on POSIX it is created with
owner-only permissions. **Tool results, including excerpts and local paths,
enter your assistant conversation** and are subject to your client's settings.

`.gitignore` excludes `config.json`, common local configs, books, databases,
virtual environments, and common key files. Keep private material outside this
repository when possible, and inspect the files staged for a commit. Share the
toolkit and your original notes; supply books or third-party code separately
under their own applicable terms.

## Verify

All tests use temporary synthetic files, including a generated PDF and EPUB;
they require no personal documents or network access.

```sh
.venv/bin/python -m unittest discover -s tests -v
RUN_MCP_PROTOCOL=1 .venv/bin/python -m unittest discover -s tests -v
```

The second command also exercises initialization and all three tools over the
actual MCP stdio protocol. The direct dependencies are pinned; transitive
dependency versions are resolved by pip.

## References

- [Python MCP SDK v1.26.0](https://github.com/modelcontextprotocol/python-sdk/tree/v1.26.0)
- [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [pypdf documentation](https://pypdf.readthedocs.io/)
