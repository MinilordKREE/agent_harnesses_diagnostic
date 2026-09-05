"""The one interface every model backend implements.

No reference source: written fresh for ahd (see docs/reuse/M0.md). AutoSaddler's
``AgentTransport`` protocol (src/autosaddler/v2/providers/base.py) is the same idea at the
agent level.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ahd.llm.types import ChatRequest, ChatResponse


@runtime_checkable
class Provider(Protocol):
    def complete(self, request: ChatRequest) -> ChatResponse:
        """Return a completion. Infra problems raise ``InfraError``; never return a fake answer."""
        ...
