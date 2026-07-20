import datetime
import hashlib
from pathlib import Path

import pandas as pd
import streamlit as st

from database import (
    create_extraction_job,
    get_monthly_api_call_count,
    save_extract_call_log,
    save_extracted_orders,
    save_raw_chat_files,
    save_training_record,
    save_unresolved_items,
    update_extraction_job_total,
)
from excel_utils import write_excel_with_text_zipcode
from resolver import CatalogIndex
from session_keys import (
    CHAT_DISPLAY_NAMES,
    CHAT_UPLOADER_KEY,
    MONTHLY_EXTRACT_LIMIT,
    REVIEW_DEFAULT_JOB_ID,
)
from services import (
    format_phone_number,
    lookup_zip_code,
    normalize_zip_code,
    parse_catalog_json,
    process_chat_file,
)
from ui.common import AppContext


MAPPING_STATUS_LABELS = {
    "exact": "✅ 확정",
    "alias": "✅ 확정",
    "typo": "🔧 자동보정",
    "inferred": "🔧 자동보정",
}


def render(ctx: AppContext) -> None:
    catalog_file, chat_files = _render_uploaders()
    time_after, time_before = _render_live_time()
    if not st.button("🚀 주문서 추출 실행", type="primary", width="stretch"):
        return
    if not _validate_inputs(ctx, catalog_file, chat_files):
        return

    files_to_process = _apply_monthly_limit(ctx, chat_files)
    if not files_to_process:
        return
    _run_extraction(
        ctx, catalog_file, files_to_process, time_after, time_before
    )


def _render_uploaders():
    columns = st.columns(2)
    with columns[0]:
        st.markdown(
            '<span class="step-badge">1</span> **카탈로그 업로드**',
            unsafe_allow_html=True,
        )
        catalog_file = st.file_uploader("카탈로그를 업로드하세요.", type=["json"])
    with columns[1]:
        st.markdown(
            '<span class="step-badge">2</span> **대화 내역 업로드**',
            unsafe_allow_html=True,
        )
        uploader_key = st.session_state.setdefault(CHAT_UPLOADER_KEY, 0)
        files = st.file_uploader(
            "카카오톡 대화 파일들을 업로드하세요.",
            type=["csv"],
            accept_multiple_files=True,
            key=f"chat_uploader_{uploader_key}",
        )
        files, display_names = _dedupe_uploaded_files(files or [])
        if files:
            st.session_state[CHAT_DISPLAY_NAMES] = display_names
            with st.expander(f"📁 업로드된 파일 {len(files)}개 보기"):
                for uploaded in files:
                    st.write(f"• {display_names.get(_file_key(uploaded), uploaded.name)}")
            if st.button("❌ 업로드 파일 전체 삭제", key="clear_chat_files"):
                st.session_state[CHAT_UPLOADER_KEY] += 1
                st.rerun()
    return catalog_file, files


def _file_key(uploaded) -> str:
    """업로드 파일의 안정적인 식별 키(파일명 + 내용 해시).

    id()는 rerun마다 객체가 재생성되면 바뀌어 session_state에 저장한 매핑이
    깨진다. 내용 해시 기반 키는 rerun 간에도 동일하게 유지된다.
    """
    digest = hashlib.sha256(uploaded.getvalue()).hexdigest()
    return f"{uploaded.name}:{digest}"


def _dedupe_uploaded_files(files: list) -> tuple[list, dict[str, str]]:
    groups: dict[str, list[tuple[bytes, object]]] = {}
    for uploaded in files:
        content = uploaded.getvalue()
        entries = groups.setdefault(uploaded.name, [])
        if not any(content == existing for existing, _ in entries):
            entries.append((content, uploaded))

    unique = []
    display_names = {}
    for name, entries in groups.items():
        for index, (_, uploaded) in enumerate(entries, start=1):
            display_name = name
            if len(entries) > 1:
                path = Path(name)
                display_name = f"{path.stem}({index}){path.suffix}"
            unique.append(uploaded)
            display_names[_file_key(uploaded)] = display_name
    return unique, display_names


def _render_live_time() -> tuple[datetime.datetime, datetime.datetime]:
    st.markdown(
        '<span class="step-badge">3</span> **라이브쇼핑 시간 입력**',
        unsafe_allow_html=True,
    )
    columns = st.columns(4)
    start_date = columns[0].date_input("시작 날짜")
    start_time = columns[1].time_input("시작 시간")
    end_date = columns[2].date_input("종료 날짜", value=start_date)
    end_time = columns[3].time_input("종료 시간", value=datetime.time(23, 59))
    return (
        datetime.datetime.combine(start_date, start_time),
        datetime.datetime.combine(end_date, end_time),
    )


def _validate_inputs(ctx, catalog_file, chat_files) -> bool:
    if not ctx.api_key:
        st.warning("API Key가 할당되지 않았습니다. 관리자에게 문의하세요.")
    elif not catalog_file:
        st.warning("카탈로그 파일을 업로드해 주세요.")
    elif not chat_files:
        st.warning("대화 내역 파일을 1개 이상 업로드해 주세요.")
    else:
        return True
    return False


def _apply_monthly_limit(ctx: AppContext, files: list) -> list:
    limit = st.session_state.get(MONTHLY_EXTRACT_LIMIT)
    if not ctx.db_conn or limit is None:
        return files
    used = get_monthly_api_call_count(ctx.db_conn, ctx.user_id)
    remaining = limit - used
    if remaining <= 0:
        st.error(
            f"이번 달 API 호출 한도({limit}회)를 모두 사용했습니다. "
            "다음 달 1일에 초기화됩니다."
        )
        return []
    if len(files) > remaining:
        st.warning(
            f"이번 달 남은 한도가 {remaining}회입니다. "
            f"파일 {len(files)}개 중 {remaining}개만 처리합니다."
        )
        return files[:remaining]
    return files


def _run_extraction(ctx, catalog_file, files, time_after, time_before) -> None:
    with st.status("주문서 추출 중입니다", expanded=True) as status:
        st.write("📋 카탈로그를 파싱 중")
        catalog = parse_catalog_json(catalog_file)
        index = CatalogIndex.build(catalog)
        orders, unresolved, raw_files = [], [], []
        job_id = _create_job(ctx, time_after)

        date_prefix = datetime.date.today().strftime("%Y%m%d")
        sequence = 1
        progress_text = st.empty()
        progress_bar = st.progress(0)
        display_names = st.session_state.get(CHAT_DISPLAY_NAMES, {})

        for position, chat_file in enumerate(files):
            filename = display_names.get(_file_key(chat_file), chat_file.name)
            progress_text.write(f"💬 채팅 내역 분석 중 ({position}/{len(files)})")
            try:
                result = process_chat_file(
                    chat_file=chat_file,
                    catalog=catalog,
                    index=index,
                    config=ctx.config,
                    api_key=ctx.api_key,
                    prompt_template=ctx.order_extraction_prompt,
                    display_name=filename,
                    time_after=time_after,
                    time_before=time_before,
                    order_number=f"{date_prefix}{sequence:03d}",
                )
            except RuntimeError as exc:
                st.error(str(exc))
                _update_progress(progress_bar, progress_text, position, len(files))
                continue

            orders.extend(result.orders)
            unresolved.extend(result.unresolved)
            raw_files.append(result.raw_file)
            if result.extracted_data and result.extracted_data.get("items"):
                sequence += 1
            _save_file_result(ctx, job_id, filename, catalog, result)
            _update_progress(progress_bar, progress_text, position, len(files))

        _save_batch_result(ctx, job_id, raw_files, unresolved)
        if job_id:
            st.session_state[REVIEW_DEFAULT_JOB_ID] = str(job_id)
        status.update(label="🎉 주문 데이터 추출이 완료되었습니다!", state="complete")

    _render_result(ctx, job_id, orders, unresolved)
    if job_id:
        st.info(
            "추출 결과가 저장되었습니다. 자동 매핑되지 않은 항목은 "
            "`🛠️ 불확정 항목 보완` 탭에서 수정할 수 있습니다."
        )


def _update_progress(bar, text, position: int, total: int) -> None:
    completed = position + 1
    bar.progress(completed / total)
    text.write(f"💬 채팅 내역 분석 중 ({completed}/{total})")


def _create_job(ctx: AppContext, live_start: datetime.datetime):
    if not ctx.db_conn:
        return None
    return create_extraction_job(
        conn=ctx.db_conn,
        user_id=ctx.user_id,
        title=datetime.datetime.now().strftime("%Y%m%d_%H%M%S"),
        live_start_time=live_start,
        model=ctx.config["gemini"]["model"],
    )


def _save_file_result(ctx, job_id, filename, catalog, result) -> None:
    if not ctx.db_conn or not job_id:
        return
    save_extract_call_log(ctx.db_conn, ctx.user_id, job_id, filename)
    if result.extracted_data:
        save_training_record(
            conn=ctx.db_conn,
            job_id=job_id,
            user_id=ctx.user_id,
            chat_filename=filename,
            catalog_data=catalog,
            chat_data=result.chat_data,
            predicted_json=result.extracted_data,
        )


def _save_batch_result(ctx, job_id, raw_files, unresolved) -> None:
    if not ctx.db_conn or not job_id:
        return
    save_raw_chat_files(ctx.db_conn, job_id, ctx.user_id, raw_files)
    save_unresolved_items(ctx.db_conn, job_id, unresolved)


def _render_result(ctx, job_id, orders, unresolved) -> None:
    unresolved_df = pd.DataFrame(unresolved, dtype=object)
    if not unresolved_df.empty and "candidate_products" in unresolved_df:
        unresolved_df["candidate_products"] = unresolved_df[
            "candidate_products"
        ].apply(lambda value: ", ".join(value) if isinstance(value, list) else (value or ""))

    if orders:
        frame = pd.DataFrame(orders, dtype=object)
        frame["phone_number"] = frame["phone_number"].apply(format_phone_number)
        if ctx.juso_api_key:
            frame["zip_code"] = frame["search_address"].apply(
                lambda address: lookup_zip_code(address, ctx.juso_api_key)
            )
            frame["zip_code"] = frame["zip_code"].apply(normalize_zip_code)
        _save_orders(ctx, job_id, frame)
        _render_orders_and_download(ctx, frame, unresolved_df)
    elif unresolved_df.empty:
        st.error("추출된 데이터가 없습니다. 원본 데이터나 API 상태를 확인해 주세요.")

    if not unresolved_df.empty:
        st.warning(
            f"⚠️ {len(unresolved_df)}건은 자동 매핑에 실패하여 검토가 필요합니다. "
            "임의로 확정하지 않았으니 아래 내역을 확인해 주세요."
        )
        st.dataframe(
            unresolved_df[
                ["chat_name", "raw_product", "raw_option", "volume", "candidate_products", "mapping_reason"]
            ],
            width="stretch",
        )


def _save_orders(ctx, job_id, frame) -> None:
    if not ctx.db_conn or not job_id:
        return
    save_extracted_orders(ctx.db_conn, job_id, frame.to_dict("records"))
    update_extraction_job_total(ctx.db_conn, job_id, len(frame))


def _render_orders_and_download(ctx, frame, unresolved_df) -> None:
    col_map = ctx.config["output_columns"]
    rename = {value: key for key, value in col_map.items() if value}

    display = frame.copy()
    display["매핑"] = display["mapping_status"].map(MAPPING_STATUS_LABELS).fillna("✅ 확정")
    display["원문"] = display.apply(
        lambda row: (
            f"{row.get('raw_product') or ''} {row.get('raw_option') or ''}".strip()
            if row.get("mapping_status") in ("typo", "inferred") else ""
        ),
        axis=1,
    )
    display = display.rename(columns=rename).reindex(
        columns=list(col_map.keys()) + ["매핑", "원문"], fill_value=""
    )
    st.dataframe(display, width="stretch")

    output = frame.rename(columns=rename).reindex(columns=list(col_map.keys()), fill_value="")
    extras = {"검토필요": unresolved_df} if not unresolved_df.empty else None
    st.download_button(
        label="📥 엑셀 파일(.xlsx) 다운로드",
        data=write_excel_with_text_zipcode(
            output, ctx.config["output"]["sheet_name"], extra_sheets=extras
        ),
        file_name=ctx.config["output"]["file_name"],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )
