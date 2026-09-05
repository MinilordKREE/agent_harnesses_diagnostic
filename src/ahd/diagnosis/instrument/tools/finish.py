"""The finish tool: its function schema + result formatting.

finish has no side effects — it just records the final answer — so its handler
only formats the tool-result content the loop appends to the transcript. Kept in
its own module so the tool set is one-file-per-tool and a new tool follows the
same shape.
"""

from __future__ import annotations

import json
from typing import Any


FINISH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": "Finish the task with a final answer or artifact path.",
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {"type": "string", "description": "Final answer, status, or artifact path."}
            },
            "required": ["answer"],
            "additionalProperties": False,
        },
    },
}


def finish_result(answer: str) -> dict[str, Any]:
    """Tool-result content acknowledging the final answer."""
    return {"content": json.dumps({"accepted": True, "answer": answer}, ensure_ascii=False)}
