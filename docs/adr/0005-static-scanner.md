# ADR-0005 — Static scanner (v0.2)

- **Status:** Accepted
- **Date:** 2026-11-11

## Context

v0.1 finds assumptions by observing and re-running tests. v0.2 adds a static
scanner that reads source code without running it, for the cases ruff does not
cover (ADR-0001): environment assumptions that matter only when the code
actually touches the environment.

## Decision

Package `assumptionradar.static`:

- **Discovery** (`discovery.py`). Walk the project root, keep files matching
  `include` globs (default `**/*.py`), drop files matching `exclude` globs and a
  default exclude list (`.git`, `.venv`, `venv`, `.tox`, `.nox`, `build`,
  `dist`, `node_modules`, `__pycache__`, tool caches, `*.egg-info`,
  `site-packages`). Patterns without `/` match any single path segment;
  patterns with `/` match the whole POSIX relative path (`**` spans
  directories). Symlinked directories are not followed. Files larger than
  `max_file_bytes` (default 1 MB) are skipped with a reason. Output is sorted by
  relative POSIX path, so results are deterministic.
- **Parsing** (`parsing.py`). Read bytes, decode with `tokenize.detect_encoding`
  (honours PEP 263 cookies and BOMs), `ast.parse`. Decode and syntax errors
  become `ScanError` records in the result; the scan continues. **Scanned code
  is never imported or executed.** Parsing uses the running interpreter's
  grammar, so code that needs a newer Python reports a syntax error.
- **Import table** (`imports.py`). Maps local names to qualified names
  (`from pathlib import Path as P` → `P = pathlib.Path`, `import os.path as
  osp` → `osp = os.path`), and resolves `Name`/`Attribute` expressions
  (`P("x").read_text` → `pathlib.Path`, `osp.join` → `os.path.join`). Unbound
  builtins resolve to `builtins.<name>`. The table is flow-insensitive (all
  import statements in the module, the last binding wins) and does not track
  rebinding by assignment; rules must keep false positives low with that in
  mind.
- **Rules** implement a small protocol: `rule_id`, `version`,
  `check(module) -> Iterable[Finding]`. A rule that raises on a file produces a
  `ScanError(kind="rule")` and does not stop the scan. The public plugin
  contract (P3-1) builds on this.
- **Cache** (`cache.py`). Optional, per file: key = SHA-256 of relative path,
  file content and the rule signature (rule ids + versions + engine version).
  Values are the file's findings as JSON. A corrupt entry is a miss. Errors are
  not cached.
- **Result.** `ScanResult` holds findings sorted by `Finding.sort_key()` with
  fingerprint collisions disambiguated (ADR-0004), errors, skipped files, and
  counters (files scanned, cache hits, misses).

## Consequences

- Static findings carry `EvidenceKind.INFERRED`; only verification produces
  `reproduced` evidence.
- Shadowing (`open = my_open`) and dynamic imports are not understood; this is
  documented as a limitation rather than guessed at.
