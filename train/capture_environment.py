"""Capture reproducibility and GPU preflight information as JSON."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any

from train.config import write_json


PACKAGES = (
    "torch",
    "transformers",
    "accelerate",
    "peft",
    "safetensors",
    "torchao",
    "mslk-cuda",
    "bitsandbytes",
    "vllm",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def run_command(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=60
        )
        return {
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "command": command,
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
        }


def main() -> None:
    args = parse_args()
    packages: dict[str, str | None] = {}
    for name in PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    commands = {
        "nvidia_smi": run_command(
            [
                "nvidia-smi",
                "--query-gpu=name,uuid,memory.total,driver_version",
                "--format=csv",
            ]
        ),
        "git_head": run_command(["git", "rev-parse", "HEAD"]),
        "git_status": run_command(["git", "status", "--short"]),
        "pip_freeze": run_command([sys.executable, "-m", "pip", "freeze"]),
    }
    value = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
        "commands": commands,
        "gpu_available": (
            commands["nvidia_smi"]["exit_code"] == 0
            and bool(commands["nvidia_smi"]["stdout"])
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, value)
    print(f"Environment saved to {args.output}")


if __name__ == "__main__":
    main()
