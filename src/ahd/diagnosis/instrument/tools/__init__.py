"""Policy-harness tool set: one module per tool, aggregated here.

This package is the facade harness.py imports from (``from tools import ...``).
Each tool module owns its schema + handler; this __init__ re-exports them with
package-relative imports so the package's internal layout can change without
touching harness.py. To add a tool: drop a new module here, re-export its schema
+ handler below, and wire it into the loop in harness.py.
"""

from __future__ import annotations

from .finish import FINISH_TOOL, finish_result
from .shell import RUN_SHELL_TOOL, run_shell_command

# The tool schemas advertised to the model, in call-priority order.
TOOL_SCHEMAS = [RUN_SHELL_TOOL, FINISH_TOOL]

__all__ = [
    "RUN_SHELL_TOOL",
    "FINISH_TOOL",
    "TOOL_SCHEMAS",
    "run_shell_command",
    "finish_result",
]
