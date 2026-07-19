from dataclasses import dataclass

import streamlit as st

from database import get_jobs_by_user


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
