"""Shared low-level helpers for the policy harness's tools.

Leaf module: no intra-package dependencies, so everything else in ``tools/`` can
import from here freely. Kept separate from any one tool so a new tool can reuse
the subprocess-output decoding / truncation without depending on shell.py.
"""

from __future__ import annotations

from typing import Any


def decode(raw: Any) -> str:
    """Decode subprocess output bytes leniently.

    Task commands often fetch non-UTF-8 content (GBK Chinese pages, gzip/binary
    fragments). errors="replace" keeps the rollout alive and the output readable
    instead of raising UnicodeDecodeError or silently dropping bytes.
    """
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def truncate(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit // 2]}\n...[truncated {len(text) - limit} chars]...\n{text[-limit // 2:]}"
