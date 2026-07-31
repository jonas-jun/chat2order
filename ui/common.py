from dataclasses import dataclass

import streamlit as st

from database import get_jobs_by_user, get_monthly_api_call_count


@st.cache_data(ttl=60)
def monthly_api_usage(_conn, user_id: str, month_key: str) -> int:
    """이번 달 API 호출 수를 조회한다. 사이드바 표시와 한도 차단이 함께 쓴다.

    ``_conn``은 해시할 수 없는 Supabase 클라이언트이므로 언더스코어 접두사로
    캐시 키에서 제외한다. ``month_key``는 값을 쓰지 않지만 캐시 키에 포함되어,
    월이 바뀌면 지난달 집계가 남지 않도록 한다.
    """
    return get_monthly_api_call_count(_conn, user_id) if _conn else 0


def clear_monthly_api_usage_cache() -> None:
    """API 호출을 기록한 직후 캐시를 비워 표시·차단 값이 즉시 반영되게 한다."""
    monthly_api_usage.clear()


@dataclass(frozen=True)
class AppContext:
    db_conn: object | None
    config: dict
    api_key: str
    user_id: str
    juso_api_key: str
    order_extraction_prompt: str
    address_to_search_prompt: str


def select_job_ui(
    db_conn,
    user_id: str,
    label: str,
    *,
    key: str,
) -> dict | None:
    """최근 작업 목록과 공통 라벨 형식의 radio 선택 UI를 표시한다."""
    jobs = get_jobs_by_user(conn=db_conn, user_id=user_id)
    if not jobs:
        return None
    labels = [_job_label(job) for job in jobs]
    selected = st.radio(
        label,
        options=range(len(jobs)),
        format_func=lambda index: labels[index],
        key=key,
    )
    return jobs[selected]


def _job_label(job: dict) -> str:
    live_start = job.get("live_start_time")
    live_text = live_start[:16].replace("T", " ") if live_start else "-"
    return (
        f"{job['title']}  |  라이브: {live_text}  |  "
        f"{job.get('total_orders', 0)}건"
    )
