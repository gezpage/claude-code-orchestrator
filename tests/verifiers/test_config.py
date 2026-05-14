from pathlib import Path

import yaml

from orchestrator.verifiers.config import load_project_config


def test_no_file_returns_none(tmp_path: Path):
    assert load_project_config(tmp_path) is None


def test_no_verification_block_returns_none(tmp_path: Path):
    (tmp_path / ".cco.yaml").write_text(yaml.dump({"other": "stuff"}))
    assert load_project_config(tmp_path) is None


def test_toolchain_pin_loaded(tmp_path: Path):
    (tmp_path / ".cco.yaml").write_text(yaml.dump({"verification": {"toolchain": "node"}}))
    cfg = load_project_config(tmp_path)
    assert cfg is not None
    assert cfg.toolchain == "node"
    assert cfg.commands is None
    assert cfg.probes is None


def test_command_override_replaces_not_merges(tmp_path: Path):
    (tmp_path / ".cco.yaml").write_text(
        yaml.dump(
            {
                "verification": {
                    "commands": [{"id": "only", "command": "true", "required": True}],
                }
            }
        )
    )
    cfg = load_project_config(tmp_path)
    assert cfg is not None
    assert cfg.commands is not None
    assert len(cfg.commands) == 1
    assert cfg.commands[0].id == "only"


def test_empty_command_list_is_explicit_override(tmp_path: Path):
    (tmp_path / ".cco.yaml").write_text(yaml.dump({"verification": {"commands": []}}))
    cfg = load_project_config(tmp_path)
    assert cfg is not None
    assert cfg.commands == ()
