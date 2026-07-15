import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def get_env(name: str, default: str = "") -> str:
    """실행 환경에서 설정값을 읽습니다."""
    return os.getenv(name, default)


def load_prompt(path: str) -> str:
    """프로젝트 루트 기준의 프롬프트 파일을 UTF-8로 읽습니다."""
    prompt_path = Path(path)
    if not prompt_path.is_absolute():
        prompt_path = PROJECT_ROOT / prompt_path
    return prompt_path.read_text(encoding="utf-8")
