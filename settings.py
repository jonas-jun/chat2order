import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent

# 서비스 기준 타임존. 배포 서버의 로컬 타임존(Railway 기본값 UTC)과 무관하게
# 월 사용량 집계 경계를 한국 시각으로 고정하기 위해 사용한다.
KST = timezone(timedelta(hours=9))


def kst_month_range(now: datetime | None = None) -> tuple[datetime, datetime]:
    """KST 기준 이번 달의 시작 시각과 다음 달 시작 시각을 반환합니다.

    배포 서버의 로컬 타임존은 UTC이므로 ``datetime.now()``를 그대로 쓰면 KST
    1일 00:00~09:00 구간에 아직 지난달로 판정된다. 기준 시각과 경계 양쪽에
    KST를 명시해 서버·DB 타임존 설정과 무관하게 같은 결과를 내도록 한다.

    반환값은 타임존을 가진 datetime이므로 ``isoformat()``에 ``+09:00``이
    포함되어, 비교 대상 컬럼이 어떤 타임존으로 해석되든 경계가 밀리지 않는다.

    ``now``에는 타임존을 가진 datetime을 넘길 수 있습니다(테스트용).
    """
    current = datetime.now(KST) if now is None else now.astimezone(KST)
    month_start = datetime(current.year, current.month, 1, tzinfo=KST)
    next_month = datetime(
        current.year + current.month // 12,
        current.month % 12 + 1,
        1,
        tzinfo=KST,
    )
    return month_start, next_month


def current_month_key(now: datetime | None = None) -> str:
    """KST 기준 'YYYY-MM' 문자열. 월이 바뀔 때 캐시를 무효화하는 데 사용합니다."""
    month_start, _ = kst_month_range(now)
    return month_start.strftime("%Y-%m")


def get_env(name: str, default: str = "") -> str:
    """실행 환경에서 설정값을 읽습니다."""
    return os.getenv(name, default)


def load_prompt(path: str) -> str:
    """프로젝트 루트 기준의 프롬프트 파일을 UTF-8로 읽습니다."""
    prompt_path = Path(path)
    if not prompt_path.is_absolute():
        prompt_path = PROJECT_ROOT / prompt_path
    return prompt_path.read_text(encoding="utf-8")
