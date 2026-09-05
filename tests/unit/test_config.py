from __future__ import annotations

from pathlib import Path

import pytest

from ahd.core.config import RunConfig, config_sha256, load_run_config
from ahd.errors import ConfigError, InfraError
from tests.conftest import REPO_ROOT

MINIMAL = "schema_version: 1\nname: t\nllm: {model: deepseek-v4-flash}\n"


@pytest.mark.parametrize("name", ["example.yaml", "example_confirmatory.yaml"])
def test_shipped_configs_validate(name: str) -> None:
    config = load_run_config(REPO_ROOT / "configs" / "runs" / name)
    assert config.schema_version == 1
    assert config.llm.provider == "deepseek"


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(MINIMAL + "temperture: 0.5\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="temperture"):
        load_run_config(path)


def test_wrong_schema_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(MINIMAL.replace("schema_version: 1", "schema_version: 2"), encoding="utf-8")
    with pytest.raises(ConfigError, match="schema_version"):
        load_run_config(path)


def test_require_clean_tree_defaults_by_kind(tmp_path: Path) -> None:
    exploratory = RunConfig.model_validate({"schema_version": 1, "name": "a"})
    confirmatory = RunConfig.model_validate(
        {"schema_version": 1, "name": "a", "kind": "confirmatory"}
    )
    overridden = RunConfig.model_validate(
        {"schema_version": 1, "name": "a", "kind": "confirmatory", "require_clean_tree": False}
    )
    assert exploratory.require_clean_tree is False
    assert confirmatory.require_clean_tree is True
    assert overridden.require_clean_tree is False


def test_missing_file_is_infra_error(tmp_path: Path) -> None:
    with pytest.raises(InfraError) as info:
        load_run_config(tmp_path / "nope.yaml")
    assert info.value.kind == "missing_file"


def test_invalid_yaml_is_config_error(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("schema_version: [1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_run_config(path)


def test_non_mapping_is_config_error(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_run_config(path)


def test_config_hash_ignores_key_order_and_comments(tmp_path: Path) -> None:
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("schema_version: 1\nname: t\nseed: 3\n# comment\n", encoding="utf-8")
    b.write_text("seed: 3\nname: t\nschema_version: 1\n", encoding="utf-8")
    assert config_sha256(load_run_config(a)) == config_sha256(load_run_config(b))
    b.write_text("seed: 4\nname: t\nschema_version: 1\n", encoding="utf-8")
    assert config_sha256(load_run_config(a)) != config_sha256(load_run_config(b))


def test_judge_and_tasks_sections_parse() -> None:
    config = load_run_config(REPO_ROOT / "configs" / "runs" / "example.yaml")
    assert config.judge.model == "deepseek-v4-pro"
    assert config.judge.temperature == 0.0
    assert config.judge.use_cache is True
    assert config.tasks is not None
    assert config.tasks.split == "validation"
    assert config.tasks.sources == ("browsecomp", "hle", "gdpval", "claw_eval")
    assert config.tasks.n is None
    minimal = RunConfig.model_validate({"schema_version": 1, "name": "a"})
    assert minimal.tasks is None and minimal.judge.model == "deepseek-v4-pro"
