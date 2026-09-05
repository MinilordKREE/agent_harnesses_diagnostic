"""Opt-in response cache: one JSON file per SHA-256 key, versioned key, atomic writes.

Adapted from: stanford-iris-lab/meta-harness @ 44b9942127847f7421db70d8c7e48407f09a3c70
Original path: reference_examples/text_classification/llm.py (``_cache_path``, ``_load_cache``,
``_save_cache``; lines 164-195)
License: MIT, Copyright (c) 2026 Yoonho Lee -- see THIRD_PARTY_NOTICES.md

Also adapted from: scaleapi/vero @ 0b0e86764d836c456aee5b8dff80d765fdbba77c
Original path: vero/src/vero/interpret/cache.py (``key_of``, ``_path`` two-char fan-out,
``put_json`` tmp+rename; lines 22-58)
License: MIT, Copyright (c) 2026 Scale AI -- see THIRD_PARTY_NOTICES.md

Changes: key is the canonical JSON of ``ChatRequest.cache_payload()`` (model, messages,
temperature, seed, max_tokens, thinking, reasoning_effort) plus ``CACHE_VERSION`` and the
provider name; a corrupt entry raises :class:`InfraError` instead of being treated as a miss
(both upstreams swallow it); writes go through ``atomic_write_text``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from ahd.core.config import StrictModel
from ahd.core.hashing import JsonValue, sha256_of
from ahd.core.io import atomic_write_text, read_json
from ahd.errors import InfraError
from ahd.llm.types import ChatRequest, ChatResponse

CACHE_VERSION = 2
"""v2 (M1): ``cache_scope`` joined the key payload."""


class CacheEntry(StrictModel):
    cache_version: int
    key: str
    provider: str
    created_at: datetime
    request: dict[str, JsonValue]
    response: ChatResponse


class ResponseCache:
    def __init__(self, root: Path, *, provider: str) -> None:
        self.root = root
        self.provider = provider
        self.hits = 0
        self.misses = 0

    def key(self, request: ChatRequest) -> str:
        return sha256_of(
            {
                "cache_version": CACHE_VERSION,
                "provider": self.provider,
                "request": request.cache_payload(),
            }
        )

    def path_for(self, key: str) -> Path:
        return self.root / self.provider / key[:2] / f"{key}.json"

    def get(self, request: ChatRequest) -> ChatResponse | None:
        """Return the cached response (marked ``cached=True``) or ``None`` on a miss."""
        key = self.key(request)
        path = self.path_for(key)
        if not path.is_file():
            self.misses += 1
            return None
        raw = read_json(path)
        try:
            entry = CacheEntry.model_validate(raw)
        except ValidationError as exc:
            raise InfraError(f"corrupt cache entry {path}:\n{exc}", kind="corrupt_file") from exc
        if entry.key != key or entry.cache_version != CACHE_VERSION:
            raise InfraError(
                f"cache entry {path} does not match its key or version", kind="corrupt_file"
            )
        self.hits += 1
        return entry.response.model_copy(update={"cached": True})

    def put(self, request: ChatRequest, response: ChatResponse) -> Path:
        key = self.key(request)
        path = self.path_for(key)
        entry = CacheEntry(
            cache_version=CACHE_VERSION,
            key=key,
            provider=self.provider,
            created_at=datetime.now(UTC),
            request=request.cache_payload(),
            response=response.model_copy(update={"cached": False}),
        )
        atomic_write_text(path, entry.model_dump_json(indent=2) + "\n")
        return path
