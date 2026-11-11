"""Optional per-file result cache for the static scanner (ADR-0005).

The cache is best-effort: unreadable or corrupt entries are misses and write
failures are ignored.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class FileCache:
    """A JSON file cache keyed by SHA-256 hex digests, separated by namespace.

    Attributes:
        hits: Number of successful lookups.
        misses: Number of lookups that found no usable entry.
    """

    def __init__(self, directory: Path, namespace: str) -> None:
        """Create a cache rooted at ``directory`` for ``namespace``.

        Args:
            directory: Root directory of the cache.
            namespace: Namespace string; different namespaces never share entries.
        """
        self._directory = directory
        self._namespace_hash = hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:16]
        self.hits = 0
        self.misses = 0

    def _entry_path(self, key: str) -> Path:
        """Return the file path for ``key``.

        Raises:
            ValueError: If ``key`` is not a 64-character lowercase hex string.
        """
        if _KEY_RE.fullmatch(key) is None:
            raise ValueError(f"invalid cache key {key!r}")
        return self._directory / self._namespace_hash / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        """Return the stored mapping for ``key``, or None on a miss.

        Raises:
            ValueError: If ``key`` is malformed.
        """
        path = self._entry_path(key)
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            self.misses += 1
            return None
        if not isinstance(payload, dict):
            self.misses += 1
            return None
        self.hits += 1
        return payload

    def put(self, key: str, data: Mapping[str, Any]) -> None:
        """Store ``data`` under ``key`` atomically. Write errors are ignored.

        Raises:
            ValueError: If ``key`` is malformed.
        """
        path = self._entry_path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(dict(data), handle, sort_keys=True)
                Path(tmp_name).replace(path)
            except BaseException:
                with contextlib.suppress(OSError):
                    Path(tmp_name).unlink()
                raise
        except OSError:
            return
