"""Fine-tune a <=4B causal language model on Chat2Order JSONL data."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from datasets import Dataset, load_dataset
from peft import LoraConfig, TaskType, get_peft_model
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
    set_seed,
)

from train.config import (
    experiment_output_dir,
    file_sha256,
    load_config,
    project_path,
    write_json,
)
from train.data import tokenize_sft_example


@dataclass
class AssistantOnlyCollator:
    """Pad inputs while retaining -100 labels for every prompt token."""

    pad_token_id: int
    pad_to_multiple_of: int = 8

    def __call__(self, features: list[dict[str, Any]]) -> dict[str, torch.Tensor]:
        maximum = max(len(feature["input_ids"]) for feature in features)
        if self.pad_to_multiple_of:
            remainder = maximum % self.pad_to_multiple_of
            if remainder:
                maximum += self.pad_to_multiple_of - remainder

        input_ids: list[list[int]] = []
        attention_masks: list[list[int]] = []
        labels: list[list[int]] = []
        for feature in features:
            padding = maximum - len(feature["input_ids"])
            input_ids.append(feature["input_ids"] + [self.pad_token_id] * padding)
            attention_masks.append(feature["attention_mask"] + [0] * padding)
            labels.append(feature["labels"] + [-100] * padding)
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "attention_mask": torch.tensor(attention_masks, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--train-file", type=Path)
    parser.add_argument("--validation-file", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--resume-from-checkpoint",
        nargs="?",
        const=True,
        help="Resume the latest checkpoint, or pass a checkpoint path.",
    )
    return parser.parse_args()


def _prepare_split(
    dataset: Dataset,
    tokenizer: Any,
    data_config: dict[str, Any],
    split_name: str,
) -> Dataset:
    def preprocess(example: dict[str, Any], index: int) -> dict[str, list[int]]:
        try:
            return tokenize_sft_example(
                example,
                tokenizer,
                max_length=int(data_config["max_length"]),
                truncation=str(data_config.get("truncation", "error")),
                prompt_head_tokens=int(data_config.get("prompt_head_tokens", 512)),
                chat_template_kwargs=data_config.get("chat_template_kwargs", {}),
            )
        except ValueError as exc:
            raise ValueError(f"{split_name} row {index}: {exc}") from exc

    return dataset.map(
        preprocess,
        with_indices=True,
        remove_columns=dataset.column_names,
        num_proc=int(data_config.get("preprocessing_num_workers", 1)),
        desc=f"Tokenizing {split_name}",
    )


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    model_config = config["model"]
    data_config = config["data"]
    run_config = config["training"]
    lora_config = config["lora"]

    model_name = args.model or str(model_config["name_or_path"])
    train_path = project_path(args.train_file or data_config["train_file"])
    validation_path = project_path(
        args.validation_file or data_config["validation_file"]
    )
    output_dir = (
        project_path(args.output_dir)
        if args.output_dir is not None
        else experiment_output_dir(config["experiment_name"])
    )
    for path in (train_path, validation_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    output_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    report_to = run_config.get("report_to", "none")
    reports_to_wandb = report_to == "wandb" or (
        isinstance(report_to, list) and "wandb" in report_to
    )
    if reports_to_wandb:
        os.environ.setdefault("WANDB_PROJECT", config.get("wandb_project", "chat2order"))
        os.environ.setdefault("WANDB_DIR", str(output_dir))
    seed = int(run_config.get("seed", 42))
    set_seed(seed)

    tokenizer = AutoTokenizer.from_pretrained(
        model_name,
        revision=model_config.get("revision", "main"),
        trust_remote_code=bool(model_config.get("trust_remote_code", False)),
        use_fast=True,
    )
    if tokenizer.chat_template is None:
        raise ValueError(f"{model_name} tokenizer has no chat template")
    if tokenizer.pad_token_id is None:
        if tokenizer.eos_token_id is None:
            raise ValueError("tokenizer must define an EOS or padding token")
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    raw = load_dataset(
        "json",
        data_files={"train": str(train_path), "validation": str(validation_path)},
        cache_dir=str(project_path(data_config.get("cache_dir", "train/cache"))),
    )
    train_dataset = _prepare_split(raw["train"], tokenizer, data_config, "train")
    validation_dataset = _prepare_split(
        raw["validation"], tokenizer, data_config, "validation"
    )

    dtype_name = str(model_config.get("dtype", "bfloat16"))
    dtypes = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}
    if dtype_name not in dtypes:
        raise ValueError(f"unsupported model.dtype: {dtype_name}")
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        revision=model_config.get("revision", "main"),
        trust_remote_code=bool(model_config.get("trust_remote_code", False)),
        dtype=dtypes[dtype_name],
        attn_implementation=model_config.get("attn_implementation", "sdpa"),
        low_cpu_mem_usage=True,
    )
    model.config.use_cache = False

    has_validation = len(validation_dataset) > 0
    training_kwargs = dict(run_config)
    training_kwargs.setdefault("eval_strategy", "steps" if has_validation else "no")
    training_kwargs.setdefault(
        "gradient_checkpointing_kwargs", {"use_reentrant": False}
    )
    training_kwargs.setdefault("dataloader_pin_memory", True)
    training_kwargs.setdefault("remove_unused_columns", False)
    training_kwargs.setdefault("data_seed", seed)
    if reports_to_wandb:
        training_kwargs.setdefault("run_name", config["experiment_name"])
    training_kwargs.update(
        output_dir=str(output_dir),
        bf16=dtype_name == "bfloat16",
        fp16=dtype_name == "float16",
        eval_strategy=training_kwargs["eval_strategy"] if has_validation else "no",
        load_best_model_at_end=has_validation,
        metric_for_best_model="eval_loss" if has_validation else None,
        greater_is_better=False if has_validation else None,
        seed=seed,
    )
    training_args = TrainingArguments(**training_kwargs)

    lora_kwargs = dict(lora_config)
    lora_kwargs.update(
        task_type=TaskType.CAUSAL_LM,
        inference_mode=False,
        revision=str(model_config.get("revision", "main")),
    )
    adapter = LoraConfig(**lora_kwargs)
    model = get_peft_model(model, adapter)
    if training_args.gradient_checkpointing:
        model.enable_input_require_grads()
    model.print_trainable_parameters()

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=validation_dataset if has_validation else None,
        data_collator=AssistantOnlyCollator(tokenizer.pad_token_id),
        processing_class=tokenizer,
    )
    train_result = trainer.train(resume_from_checkpoint=args.resume_from_checkpoint)
    trainer.save_state()

    final_dir = output_dir / "final_adapter"
    model.config.use_cache = True
    trainer.save_model(str(final_dir))
    tokenizer.save_pretrained(final_dir)
    manifest = {
        "base_model": model_name,
        "base_model_hub_id": model_config["hub_id"],
        "base_model_revision": model_config.get("revision", "main"),
        "config": config,
        "data": {
            "train_file": str(train_path),
            "train_sha256": file_sha256(train_path),
            "train_rows": len(train_dataset),
            "validation_file": str(validation_path),
            "validation_sha256": file_sha256(validation_path),
            "validation_rows": len(validation_dataset),
        },
        "metrics": train_result.metrics,
        "torch_version": torch.__version__,
    }
    write_json(final_dir / "training_manifest.json", manifest)

    artifact_name = None
    if reports_to_wandb and trainer.is_world_process_zero():
        import wandb

        if wandb.run is None:
            raise RuntimeError("W&B run is not initialized")
        artifact = wandb.Artifact(
            name=config["experiment_name"],
            type="model",
            metadata={
                "base_model_hub_id": model_config["hub_id"],
                "base_model_revision": model_config.get("revision", "main"),
            },
        )
        artifact.add_dir(str(final_dir))
        wandb.run.log_artifact(artifact, aliases=["latest"])
        artifact_name = f"{config['experiment_name']}:latest"
        wandb.finish()

    print(
        json.dumps(
            {
                "final_adapter": str(final_dir),
                "wandb_artifact": artifact_name,
                **train_result.metrics,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
