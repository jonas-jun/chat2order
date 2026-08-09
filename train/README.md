# Chat2Order SFT

검수·비식별화된 채팅에서 주문 JSON을 추출하도록 2B~4B급 언어 모델을 LoRA SFT하는 코드입니다. 로컬에 저장된 Qwen3.5 2B·4B와 Gemma 4 E4B-it(effective 4.5B, embedding 포함 raw 8B), Gemma 4 E2B-it(raw 5.15B)를 비교합니다. Qwen3.5와 Gemma 4는 멀티모달 체크포인트지만 transformers v5의 `AutoModelForCausalLM`이 텍스트 백본만 로드하므로 학습·추론은 텍스트 전용으로 동작합니다. 단일 H100에서 BF16으로 실행하도록 설정했습니다.

모델은 `order_name`, `phone_number`, `address`, `items[].raw_product`, `items[].raw_option`, `items[].volume`까지만 추출합니다. 카탈로그의 최종 `product`/`option` 선택은 학습 모델에 맡기지 않고 기존 `CatalogResolver`가 처리해야 합니다.

## 파일 구성

- `configs/qwen3_5_2b.yaml`: Qwen3.5-2B 효율 비교 설정
- `configs/qwen3_5_4b.yaml`: Qwen3.5-4B 품질 기준 설정
- `configs/gemma4_e4b.yaml`: Gemma 4 E4B 비교 설정
- `configs/gemma4_e2b.yaml`: Gemma 4 E2B 비교 설정(E4B와 모델만 다르고 나머지 조건 동일)
- `data.py`: 입력 포맷, 스키마 검증, assistant-only label 생성
- `validate_dataset.py`: schema, split 누수, token 길이 사전 검사
- `sft.py`: Transformers Trainer + PEFT LoRA 학습
- `predict.py`: adapter 또는 병합 모델의 결정론적 추론
- `merge_lora.py`: adapter를 기반 모델에 병합

## 1. 환경 준비

`train/requirements.txt`는 기록된 실험이 실제로 사용한 버전으로 고정되어 있습니다. 근거는 네 SFT 실행의 `training_manifest.json`(torch 2.13.0+cu130)과 2026-07-22 평가의 `scores/manifest.json`입니다.

```bash
conda create -n c2o_train python=3.13 -y
conda activate c2o_train
python -m pip install --upgrade pip
python -m pip install -r train/requirements.txt
```

`google-genai`는 `train/evaluate.py`의 Gemini backend에만 필요하지만, 이 파일만으로 학습과 평가를 모두 실행할 수 있도록 포함했습니다. 최상위 `requirements.txt`와 같은 값이므로 두 파일의 핀을 함께 갱신하세요.

GPU와 BF16 지원 여부를 확인합니다.

```bash
python -c "import torch; print(torch.cuda.get_device_name(0)); print(torch.cuda.is_bf16_supported())"
```

Hugging Face 모델 다운로드를 위해 필요한 경우 로그인합니다.

```bash
huggingface-cli login
```

Qwen3.5와 Gemma 4는 모두 별도 사용 동의 없이 내려받을 수 있습니다(Gemma 4는 Apache 2.0으로 라이선스가 변경되었습니다). legacy `gemma-2-2b-it`만 Hugging Face 모델 페이지에서 Google 사용 조건 동의가 필요합니다.

Qwen3.5의 gated-DeltaNet(linear attention) 계층은 `causal-conv1d`와 `fla` 커널이 있으면 빨라집니다. 없어도 PyTorch fallback으로 동작하므로 선택 사항이며, `requirements.txt`의 주석을 해제해 설치합니다.

## 2. 학습 데이터

설정은 기존 privacy-safe split을 직접 읽습니다.

```text
working/dataset/recovery_v1/splits/
├── train.jsonl
├── validation.jsonl
└── test.jsonl
```

각 줄의 계약은 다음과 같습니다.

```json
{
  "messages": [{"user": "customer", "message": "..."}],
  "target": {
    "order_name": null,
    "phone_number": null,
    "address": null,
    "items": [
      {"raw_product": "원문 상품", "raw_option": "원문 옵션", "volume": 1}
    ]
  }
}
```

원본 `train/dataset/training_data_rows.csv`를 직접 학습에 넣지 마세요. 기존 export가 검수 결과 반영, 개인정보 가명화, content-group split을 수행한 뒤 만든 JSONL만 사용합니다. `train/dataset/`과 `train/cache/`는 Git에서 제외되어 있습니다.

먼저 다운로드 없이 schema와 split 누수를 검사합니다.

```bash
python -m train.validate_dataset \
  --config train/configs/qwen3_5_4b.yaml \
  --schema-only
```

그다음 실제 tokenizer로 최대 길이까지 검사합니다. 기본 `truncation: error`는 긴 샘플을 조용히 자르지 않고 중단시킵니다.

```bash
python -m train.validate_dataset \
  --config train/configs/qwen3_5_4b.yaml
```

길이 초과가 있다면 우선 `max_length`를 늘립니다. 부득이한 경우에만 설정을 `truncation: keep_ends`로 바꾸세요. 이 모드는 지시문 앞부분과 최근 대화 뒷부분을 남기므로 대화 중간의 고객 정보가 빠질 수 있습니다.

## 3. 학습

각 설정 파일 최상단의 `experiment_name`이 실행 이름이자 출력 디렉터리 이름입니다. checkpoint와 최종 adapter는 `/workspace/storage/paip-kelp-dev/personal-jh/c2o/output/<experiment_name>/` 아래에 저장됩니다.

`training`과 `lora` 섹션의 키는 각각 Transformers `TrainingArguments`와 PEFT `LoraConfig`의 인자명을 그대로 사용합니다. 출력 경로, 모델 dtype에서 파생되는 precision, validation 연동 값처럼 실행 시 결정되는 인자는 코드가 설정값보다 우선합니다.

기본 logging은 Weights & Biases를 사용합니다. 처음 한 번 API key로 로그인합니다.

```bash
conda activate c2o_train
wandb login
```

W&B project는 config 최상단의 `wandb_project`, run 이름은 `experiment_name`을 사용합니다. 학습이 끝나면 `final_adapter/` 전체가 `<experiment_name>:latest` model Artifact로 업로드됩니다. 온라인 전송 없이 먼저 확인하려면 학습 명령 앞에 `WANDB_MODE=offline`을 지정할 수 있습니다. 로컬 W&B 파일은 각 experiment 출력 디렉터리의 `wandb/` 아래에 저장됩니다.

`model.name_or_path`는 학습 장비의 로컬 모델 경로이고, `model.hub_id`와 `model.revision`은 다른 장비에서 동일한 base model을 재현하기 위한 Hugging Face ID와 고정 commit입니다.

Qwen3.5 2B 효율 비교 명령입니다.

```bash
CUDA_VISIBLE_DEVICES=0 python -m train.sft \
  --config train/configs/qwen3_5_2b.yaml
```

Qwen3.5 4B 품질 기준 명령입니다.

```bash
CUDA_VISIBLE_DEVICES=0 python -m train.sft \
  --config train/configs/qwen3_5_4b.yaml
```

Gemma 4 E4B 비교 학습은 설정만 바꿉니다.

```bash
CUDA_VISIBLE_DEVICES=0 python -m train.sft \
  --config train/configs/gemma4_e4b.yaml
```

Gemma 4 E2B는 같은 계열에서 모델 크기를 줄였을 때의 효과를 보기 위한 설정입니다. 모델 경로, `hub_id`, `revision`, `experiment_name`만 다르고 데이터·LoRA·하이퍼파라미터는 E4B와 동일합니다.

```bash
CUDA_VISIBLE_DEVICES=0 python -m train.sft \
  --config train/configs/gemma4_e2b.yaml
```

중단된 실행은 마지막 checkpoint부터 재개할 수 있습니다.

```bash
CUDA_VISIBLE_DEVICES=0 python -m train.sft \
  --config train/configs/qwen3_5_4b.yaml \
  --resume-from-checkpoint
```

특정 checkpoint를 지정하려면 `--resume-from-checkpoint /workspace/storage/paip-kelp-dev/personal-jh/c2o/output/qwen3_5-4b-base/checkpoint-100`처럼 경로를 전달합니다.

기본 effective batch size는 `4 × gradient_accumulation_steps 4 = 16`입니다. OOM이면 `per_device_train_batch_size`를 2 또는 1로 내리고 같은 비율로 `gradient_accumulation_steps`를 올립니다. H100에서는 `bfloat16`, TF32, fused AdamW, gradient checkpointing을 사용합니다. `flash-attn`은 별도 빌드 의존성을 피하기 위해 기본값에서 제외하고 PyTorch SDPA를 사용합니다.

최종 adapter와 재현 정보는 아래에 저장됩니다.

```text
/workspace/storage/paip-kelp-dev/personal-jh/c2o/output/qwen3_5-4b-base/final_adapter/
├── adapter_config.json
├── adapter_model.safetensors
├── tokenizer...
└── training_manifest.json
```

manifest에는 기반 모델, 전체 설정, split SHA-256, 행 수, 학습 metric이 기록됩니다.

### 다른 장비에서 W&B Artifact 사용

다른 장비에서는 W&B에서 adapter를 내려받고, manifest에 기록된 Hugging Face ID와 commit으로 동일한 base model을 불러옵니다.

```python
import json
from pathlib import Path

import torch
import wandb
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

api = wandb.Api()
artifact = api.artifact(
    "<entity>/chat2order/qwen3_5-4b-base:latest",
    type="model",
)
adapter_dir = Path(artifact.download())
manifest = json.loads(
    (adapter_dir / "training_manifest.json").read_text(encoding="utf-8")
)

base_model = AutoModelForCausalLM.from_pretrained(
    manifest["base_model_hub_id"],
    revision=manifest["base_model_revision"],
    dtype=torch.bfloat16,
    device_map="auto",
)
model = PeftModel.from_pretrained(base_model, adapter_dir)
tokenizer = AutoTokenizer.from_pretrained(adapter_dir)
model.eval()
```

저장소의 추론 CLI도 manifest의 `base_model_hub_id`를 자동 사용합니다. 별도 로컬 base model을 사용하려면 `--base-model`에 경로를 지정합니다.

```bash
python -m train.predict \
  --model ./artifacts/qwen3_5-4b-base \
  --base-model /local/models/Qwen--Qwen3.5-4B \
  --input test.jsonl \
  --output predictions.jsonl
```

## 4. 추론과 병합

학습 중 모델 선택에는 validation만 사용하고 `test.jsonl`은 최종 후보가 정해진 뒤 한 번 평가하는 용도로 남겨둡니다. 먼저 소량을 추론해 JSON 파싱률과 결과를 확인합니다.

```bash
python -m train.predict \
  --model /workspace/storage/paip-kelp-dev/personal-jh/c2o/output/qwen3_5-4b-base/final_adapter \
  --input working/dataset/recovery_v1/splits/test.jsonl \
  --output /workspace/storage/paip-kelp-dev/personal-jh/c2o/output/qwen3_5-4b-base/test_predictions.jsonl \
  --limit 10
```

`predict.py`는 모델 디렉터리의 `training_manifest.json`에서 `chat_template_kwargs`를 읽어 학습과 동일한 prompt(예: Qwen thinking off)를 구성합니다. manifest가 없는 모델은 `--chat-template-kwargs '{"enable_thinking": false}'`처럼 직접 지정하세요. 길이 초과 등으로 실패한 행은 실행을 멈추지 않고 출력의 `parse_error`에 기록됩니다.

서빙 시 adapter를 별도로 로드하지 않으려면 기반 모델에 병합합니다. 출력 디렉터리는 비어 있거나 존재하지 않아야 합니다. manifest도 함께 복사됩니다.

```bash
python -m train.merge_lora \
  --adapter /workspace/storage/paip-kelp-dev/personal-jh/c2o/output/qwen3_5-4b-base/final_adapter \
  --output-dir /workspace/storage/paip-kelp-dev/personal-jh/c2o/output/qwen3_5-4b-merged
```

## 중요한 학습 특성

- 모델별 control token을 직접 하드코딩하지 않고 tokenizer의 chat template을 사용합니다.
- 입력 prompt token label은 `-100`이며 assistant의 정답 JSON token에만 loss가 적용됩니다.
- 정답 JSON key 순서와 compact serialization을 고정해 불필요한 출력 변동을 줄입니다.
- Qwen3.5는 thinking이 기본 활성이므로 `enable_thinking: false`로 학습·추론 prompt에 동일하게 빈 `<think></think>` 블록을 넣습니다. Gemma 4는 thinking 기본 비활성이며 설정에 명시해 두었습니다.
- Qwen3.5의 LoRA target에는 full-attention(`q/k/v/o_proj`)뿐 아니라 gated-DeltaNet 계층(`in_proj_qkvz`, `in_proj_ba`, `out_proj`)도 포함해 전체 계층의 3/4을 차지하는 linear attention에도 adapter가 붙습니다.
- adapter_config.json에 base 모델 revision을 기록해 추론·병합이 학습과 같은 체크포인트를 로드합니다.
- test split을 Trainer에 전달하지 않아 hyperparameter 선택 과정의 누수를 방지합니다.
