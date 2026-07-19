import streamlit as st

from database import get_raw_files_by_job
from services import search_keyword_in_raw_csv
from session_keys import (
    SEARCH_PAGE,
    SEARCH_RESULTS,
    SEARCH_RESULTS_KEYWORD,
    SEARCH_TRIGGER,
)
from ui.common import AppContext, select_job_ui


def render(ctx: AppContext) -> None:
    if not ctx.db_conn:
        st.warning("DB 연결이 설정되지 않아 검색할 수 없습니다.")
        return

    job = select_job_ui(
        ctx.db_conn,
        ctx.user_id,
        "검색할 추출 작업을 선택하세요 (최근 5건)",
        key="search_job_radio",
    )
    if not job:
        st.info("저장된 추출 이력이 없습니다. 먼저 주문서 추출을 실행해 주세요.")
        return

    keyword = st.text_input(
        "검색 키워드 (대화 내용에서 정확히 포함된 파일을 찾습니다)",
        key="search_keyword_input",
        placeholder="예: 블랙, 환불, 입금",
        on_change=lambda: st.session_state.update({SEARCH_TRIGGER: True}),
    )
    triggered = st.session_state.pop(SEARCH_TRIGGER, False)
    if st.button("🔍 검색", type="primary", key="search_run_btn") or triggered:
        _search(ctx, job, keyword)

    result = st.session_state.get(SEARCH_RESULTS)
    if not result or result.get("job_id") != job["id"]:
        return
    _render_results(result)


def _search(ctx: AppContext, job: dict, keyword: str) -> None:
    if not keyword.strip():
        st.warning("검색 키워드를 입력해 주세요.")
        return
    raw_files = get_raw_files_by_job(conn=ctx.db_conn, job_id=job["id"])
    if not raw_files:
        st.session_state[SEARCH_RESULTS] = None
        st.info(
            "이 작업은 원본 대화가 저장되지 않았습니다. "
            "(채팅 검색 기능 적용 이전이거나 DB 미연결 상태로 추출된 작업입니다.)"
        )
        return

    live_date = job.get("live_start_time", "")[:10] or None
    items = []
    for raw_file in raw_files:
        if search_keyword_in_raw_csv(
            raw_file.get("content", ""), keyword, live_date=live_date
        ):
            items.append(
                {
                    "id": raw_file["id"],
                    "filename": raw_file.get("filename"),
                    "content": raw_file.get("content", ""),
                }
            )
    st.session_state[SEARCH_RESULTS] = {
        "keyword": keyword,
        "job_id": job["id"],
        "items": items,
        "total": len(raw_files),
    }


def _render_results(result: dict) -> None:
    items = result["items"]
    st.caption(
        f"'{result['keyword']}' 검색 결과: {len(items)}개 파일 매칭 "
        f"(전체 {result['total']}개 중)"
    )
    if not items:
        st.info("매칭되는 파일이 없습니다.")
        return

    page_size = 20
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    if st.session_state.get(SEARCH_RESULTS_KEYWORD) != result["keyword"]:
        st.session_state[SEARCH_PAGE] = 0
        st.session_state[SEARCH_RESULTS_KEYWORD] = result["keyword"]
    page = min(st.session_state.get(SEARCH_PAGE, 0), total_pages - 1)

    header = st.columns([6, 1])
    header[0].markdown("**채팅명**")
    header[1].markdown("**다운로드**")
    for item in items[page * page_size : (page + 1) * page_size]:
        columns = st.columns([6, 1])
        columns[0].write(item["filename"])
        columns[1].download_button(
            label="📥",
            data=item["content"].encode("utf-8-sig"),
            file_name=item["filename"],
            mime="text/csv",
            key=f"search_dl_{item['id']}",
        )

    if total_pages <= 1:
        return
    st.caption(f"{page + 1} / {total_pages} 페이지")
    navigation = st.columns([1, 1, 8])
    if navigation[0].button("◀ 이전", key="search_prev", disabled=page == 0):
        st.session_state[SEARCH_PAGE] = page - 1
        st.rerun()
    if navigation[1].button(
        "다음 ▶", key="search_next", disabled=page >= total_pages - 1
    ):
        st.session_state[SEARCH_PAGE] = page + 1
        st.rerun()
