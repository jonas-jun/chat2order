"""Configuration helpers shared by the command-line training tools."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = Path("/workspace/storage/paip-kelp-dev/personal-jh/c2o/output")


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("config must be a YAML object")
    experiment_name = config.get("experiment_name")
    if not isinstance(experiment_name, str) or not experiment_name.strip():
        raise ValueError("config field 'experiment_name' must be a non-empty string")
    if (
        experiment_name != experiment_name.strip()
        or experiment_name in {".", ".."}
        or "/" in experiment_name
        or "\\" in experiment_name
    ):
        raise ValueError(
            "config field 'experiment_name' must be a single directory name "
            "without leading or trailing whitespace"
        )
    wandb_project = config.get("wandb_project")
    if wandb_project is not None and (
        not isinstance(wandb_project, str) or not wandb_project.strip()
    ):
        raise ValueError("config field 'wandb_project' must be a non-empty string")
    for section in ("model", "data", "training", "lora"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"config section '{section}' is required")
    hub_id = config["model"].get("hub_id")
    if not isinstance(hub_id, str) or not hub_id.strip():
        raise ValueError("config field 'model.hub_id' must be a non-empty string")
    return config


def experiment_output_dir(experiment_name: str) -> Path:
    """Return the checkpoint/output directory for an experiment."""

    return OUTPUT_ROOT / experiment_name


def project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
