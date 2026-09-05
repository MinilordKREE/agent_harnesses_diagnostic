from __future__ import annotations

import pytest

from ahd.errors import ConfigError
from ahd.harness.components import LAYERS, ComponentManifest, parse_symbol, resolve_spans
from tests.conftest import REPO_ROOT

SEED = REPO_ROOT / "third_party" / "evo-bench" / "policy_harness_seed"
MANIFEST = REPO_ROOT / "configs" / "harness" / "seed_components.yaml"

pytestmark = pytest.mark.skipif(
    not (SEED / "harness.py").is_file(), reason="submodule not checked out"
)


def test_manifest_loads_with_layers_and_patchable() -> None:
    manifest = ComponentManifest.load(MANIFEST)
    assert len(manifest.components) == 23
    assert {c.layer for c in manifest.components} <= set(LAYERS)
    assert manifest.by_id("tools_injected").patchable is False
    assert manifest.by_id("loop").patchable is True
    with pytest.raises(ConfigError):
        manifest.by_id("nope")


def test_parse_symbol_grammar() -> None:
    assert parse_symbol("system_prompt.md").model_dump() == {
        "path": "system_prompt.md",
        "qualname": None,
        "anchor": None,
    }
    assert (
        parse_symbol("agent/loop.py:run_policy_loop@components.model.create").anchor
        == "components.model.create"
    )
    assert parse_symbol("harness.json:max_steps").qualname == "max_steps"


def test_every_seed_symbol_resolves_and_locate_is_narrowest() -> None:
    manifest = ComponentManifest.load(MANIFEST)
    resolved = resolve_spans(manifest, SEED)
    assert resolved.unresolved() == ()
    external = [s for s in resolved.spans if s.kind == "external"]
    assert {s.component_id for s in external} == {"tools_injected"}
    loop_source = (SEED / "agent" / "loop.py").read_text().splitlines()
    call_line = next(
        i for i, line in enumerate(loop_source, start=1) if "components.model.create(" in line
    )
    location = resolved.locate("agent/loop.py", call_line)
    assert location is not None and location.exact
    assert location.component_id == "model_client"
    wall_line = next(
        i for i, line in enumerate(loop_source, start=1) if "elapsed >= wall_clock_seconds" in line
    )
    assert resolved.locate("agent/loop.py", wall_line).component_id == "budget"  # type: ignore[union-attr]
    shell_source = (SEED / "tools" / "shell.py").read_text().splitlines()
    popen_line = next(
        i for i, line in enumerate(shell_source, start=1) if "subprocess.Popen(" in line
    )
    assert resolved.locate("tools/shell.py", popen_line).component_id == "tool_shell"  # type: ignore[union-attr]
    assert resolved.locate("system_prompt.md", 3).component_id == "system_prompt"  # type: ignore[union-attr]
    fallback = resolved.locate("agent/state.py", 1)
    assert (
        fallback is not None and fallback.exact is False and "context_window" in fallback.candidates
    )
    assert resolved.locate("nope.py", 1) is None
    key_line = next(
        i
        for i, line in enumerate((SEED / "harness.json").read_text().splitlines(), start=1)
        if '"max_steps"' in line
    )
    assert resolved.locate("harness.json", key_line).component_id == "budget"  # type: ignore[union-attr]


def test_diff_to_components_maps_hunks() -> None:
    manifest = ComponentManifest.load(MANIFEST)
    resolved = resolve_spans(manifest, SEED)
    loop_source = (SEED / "agent" / "loop.py").read_text().splitlines()
    call_line = next(
        i for i, line in enumerate(loop_source, start=1) if "components.model.create(" in line
    )
    diff = (
        "--- a/agent/loop.py\n+++ b/agent/loop.py\n"
        f"@@ -{call_line},1 +{call_line},1 @@\n-{loop_source[call_line - 1]}\n"
        f"+{loop_source[call_line - 1]}  # touched\n"
        "--- /dev/null\n+++ b/agent/memory.py\n@@ -0,0 +1 @@\n+MEMORY = []\n"
    )
    mappings = resolved.diff_to_components(diff)
    assert [m.file for m in mappings] == ["agent/loop.py", "agent/memory.py"]
    assert mappings[0].component_ids == ("model_client",) and mappings[0].exact
    assert (
        mappings[1].status == "created"
        and mappings[1].component_ids == ()
        and not mappings[1].exact
    )
