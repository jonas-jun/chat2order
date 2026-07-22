import json
from pathlib import Path

import pytest

from train.config import OUTPUT_ROOT, experiment_output_dir, load_config


def _config(experiment_name: str) -> str:
    return f"""experiment_name: {json.dumps(experiment_name)}
model: {{hub_id: test/model}}
data: {{}}
training: {{}}
lora: {{}}
"""


def test_experiment_output_dir_uses_shared_output_root() -> None:
    assert experiment_output_dir("qwen-test") == OUTPUT_ROOT / "qwen-test"


def test_load_config_accepts_wandb_project(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(_config("qwen-test") + "wandb_project: chat2order\n", encoding="utf-8")

    assert load_config(path)["wandb_project"] == "chat2order"


@pytest.mark.parametrize("experiment_name", ["", "..", "nested/name", " nested"])
def test_load_config_rejects_unsafe_experiment_name(
    tmp_path: Path, experiment_name: str
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(_config(experiment_name), encoding="utf-8")

    with pytest.raises(ValueError, match="experiment_name"):
        load_config(path)
