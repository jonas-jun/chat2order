"""추출 작업을 파일별로 검수하고 확정 XLSX와 학습 정답을 저장하는 탭."""

from __future__ import annotations

import uuid

import pandas as pd
import streamlit as st

from database import (
    confirm_review,
    get_jobs_by_user,
    get_latest_review_version,
    get_orders_by_job,
    get_review_draft,
    get_training_records_by_job,
    save_review_draft,
)
from excel_utils import write_excel_with_text_zipcode
from review_service import (
    COMPLETED_FILE_STATUSES,
    build_initial_snapshot,
    build_training_labels,
    classify_file_review,
    decode_json,
    normalize_catalog,
    renumber_snapshot,
    snapshot_hash,
    snapshot_to_orders,
    validate_file_review,
    validate_snapshot,
)
from services import format_phone_number, normalize_zip_code
from session_keys import (
    REVIEW_CONFIRMED_REVISION,
    REVIEW_DEFAULT_JOB_ID,
    REVIEW_JOB_ID,
    REVIEW_SELECTED_FILE_ID,
    REVIEW_SNAPSHOT,
    REVIEW_TRAINING_RECORDS,
)
from ui.common import AppContext


STATUS_LABELS = {
    "unreviewed": "미확인",
    "accepted": "예측 승인",
    "corrected": "수정됨",
    "business_corrected": "업무용 수정",
    "no_order_confirmed": "주문 없음 확인",
}


def render(ctx: AppContext) -> None:
    if not ctx.db_conn:
        st.warning("DB 연결이 설정되지 않아 검수 기능을 사용할 수 없습니다.")
        return

    try:
        jobs = get_jobs_by_user(ctx.db_conn, ctx.user_id, limit=20)
    except Exception as exc:
        st.error(f"검수 작업 목록을 불러오지 못했습니다: {exc}")
        return
    if not jobs:
        st.info("검수할 추출 작업이 없습니다. 먼저 주문서 추출을 실행해 주세요.")
        return

    job = _select_job(jobs)
    if not job:
        return
    if st.session_state.get(REVIEW_JOB_ID) != str(job["id"]):
        if not _load_job(ctx, job):
            return

    snapshot = st.session_state.get(REVIEW_SNAPSHOT)
    records = st.session_state.get(REVIEW_TRAINING_RECORDS) or []
    if not snapshot:
        st.info("이 작업에는 검수할 학습 데이터가 없습니다.")
        return

    _render_summary(job, snapshot)
    selected = _select_file(snapshot)
    if selected:
        record = next(
            (
                row
                for row in records
                if str(row.get("id")) == selected.get("training_data_id")
            ),
            {},
        )
        _render_file_editor(ctx, snapshot, selected, record)
    _render_confirmation(ctx, job, snapshot)


def _select_job(jobs: list[dict]) -> dict | None:
    default_id = str(st.session_state.get(REVIEW_DEFAULT_JOB_ID) or "")
    current_id = str(st.session_state.get(REVIEW_JOB_ID) or default_id)
    index = next(
        (i for i, job in enumerate(jobs) if str(job["id"]) == current_id),
        0,
    )
    selected = st.selectbox(
        "검수할 추출 작업",
        options=range(len(jobs)),
        index=index,
        format_func=lambda i: _job_label(jobs[i]),
        key="review_job_select",
    )
    return jobs[selected] if selected is not None else None


def _job_label(job: dict) -> str:
    status = {
        "pending": "검수 대기",
        "in_progress": "검수 중",
        "confirmed": f"확정 r{job.get('current_confirmed_revision') or 1}",
        "reopened": "재검수 중",
    }.get(job.get("review_status"), "미검수")
    return f"{job.get('title')} · {status} · {job.get('total_orders', 0)}건"


def _load_job(ctx: AppContext, job: dict) -> bool:
    try:
        records = get_training_records_by_job(ctx.db_conn, job["id"])
        if not records:
            st.session_state[REVIEW_JOB_ID] = str(job["id"])
            st.session_state[REVIEW_SNAPSHOT] = None
            st.session_state[REVIEW_TRAINING_RECORDS] = []
            return True
        catalog = normalize_catalog(records[0].get("catalog_json"))
        latest = get_latest_review_version(ctx.db_conn, job["id"])
        draft = get_review_draft(ctx.db_conn, job["id"])
        if draft:
            snapshot = decode_json(draft.get("snapshot_json"), {})
            revision = int(draft.get("base_revision") or 0)
        elif latest:
            snapshot = decode_json(latest.get("snapshot_json"), {})
            revision = int(latest.get("revision") or 0)
        else:
            snapshot = build_initial_snapshot(
                str(job["id"]),
                records,
                catalog,
                get_orders_by_job(ctx.db_conn, job["id"]),
                filename_prefix=ctx.config.get("csv", {}).get("filename_prefix", ""),
            )
            revision = 0
        snapshot["base_revision"] = revision
        st.session_state[REVIEW_JOB_ID] = str(job["id"])
        st.session_state[REVIEW_SNAPSHOT] = snapshot
        st.session_state[REVIEW_TRAINING_RECORDS] = records
        st.session_state[REVIEW_CONFIRMED_REVISION] = revision
        first = (snapshot.get("files") or [{}])[0]
        st.session_state[REVIEW_SELECTED_FILE_ID] = first.get("training_data_id")
        return True
    except Exception as exc:
        st.error(
            "검수 데이터를 불러오지 못했습니다. "
            "`migrations/001_order_reviews.sql` 적용 여부를 확인해 주세요. "
            f"({exc})"
        )
        return False


def _render_summary(job: dict, snapshot: dict) -> None:
    files = snapshot.get("files") or []
    completed = sum(
        1 for file_review in files
        if file_review.get("review_status") in COMPLETED_FILE_STATUSES
    )
    orders = sum(len(file_review.get("items") or []) for file_review in files)
    columns = st.columns(4)
    columns[0].metric("전체 파일", len(files))
    columns[1].metric("확인 완료", f"{completed}/{len(files)}")
    columns[2].metric("현재 주문 행", orders)
    columns[3].metric("기준 revision", snapshot.get("base_revision", 0))
    st.progress(completed / len(files) if files else 0)
    if job.get("review_status") == "confirmed":
        st.info("확정된 작업입니다. 수정 후 다시 확정하면 새 revision으로 저장됩니다.")


def _select_file(snapshot: dict) -> dict | None:
    files = snapshot.get("files") or []
    if not files:
        return None
    current = st.session_state.get(REVIEW_SELECTED_FILE_ID)
    index = next(
        (i for i, item in enumerate(files) if item["training_data_id"] == current),
        0,
    )
    selected_index = st.selectbox(
        "검수할 채팅 파일",
        options=range(len(files)),
        index=index,
        format_func=lambda i: (
            f"{STATUS_LABELS.get(files[i].get('review_status'), '미확인')} · "
            f"{files[i].get('chat_filename')} · {len(files[i].get('items') or [])}건"
        ),
        key=f"review_file_select_{snapshot['job_id']}",
    )
    selected = files[selected_index]
    st.session_state[REVIEW_SELECTED_FILE_ID] = selected["training_data_id"]
    return selected


def _render_file_editor(
    ctx: AppContext,
    snapshot: dict,
    file_review: dict,
    record: dict,
) -> None:
    chat_data = decode_json(record.get("chat_json"), []) or []
    catalog = normalize_catalog(record.get("catalog_json"))
    with st.expander("💬 원문 대화", expanded=True):
        if not chat_data:
            st.caption("저장된 원문 대화가 없습니다.")
        for index, message in enumerate(chat_data, start=1):
            if isinstance(message, dict):
                sender = message.get("user") or "-"
                body = str(message.get("message") or "")
            else:
                sender, body = "-", str(message)
            marker = " 🟧" if "[주문완료]" in body else ""
            st.markdown(f"**{index}. {sender}{marker}**  \n{body}")

    with st.expander("🤖 최초 모델 예측"):
        st.json(decode_json(record.get("predicted_json"), {}) or {})

    key_prefix = f"review_{snapshot['job_id']}_{file_review['training_data_id']}"
    with st.form(f"{key_prefix}_form"):
        st.markdown("#### 고객정보")
        columns = st.columns(2)
        order_name = columns[0].text_input(
            "수령자명", value=file_review.get("order_name") or "", key=f"{key_prefix}_name"
        )
        phone = columns[1].text_input(
            "전화번호", value=file_review.get("phone_number") or "", key=f"{key_prefix}_phone"
        )
        address = st.text_input(
            "전체 주소", value=file_review.get("address") or "", key=f"{key_prefix}_address"
        )
        columns = st.columns(2)
        search_address = columns[0].text_input(
            "우편번호 검색용 주소",
            value=file_review.get("search_address") or "",
            key=f"{key_prefix}_search_address",
        )
        zip_code = columns[1].text_input(
            "우편번호", value=file_review.get("zip_code") or "", key=f"{key_prefix}_zip"
        )

        st.markdown("#### 주문 항목")
        editor_frame = _items_frame(file_review.get("items") or [])
        edited = st.data_editor(
            editor_frame,
            num_rows="dynamic",
            width="stretch",
            hide_index=True,
            column_order=(
                "raw_product", "raw_option", "product", "option", "volume",
                "mapping_status", "mapping_reason", "row_id",
            ),
            column_config={
                "raw_product": st.column_config.TextColumn("원문 상품"),
                "raw_option": st.column_config.TextColumn("원문 옵션"),
                "product": st.column_config.SelectboxColumn(
                    "상품명", options=list(catalog.keys())
                ),
                "option": st.column_config.SelectboxColumn(
                    "옵션명",
                    options=sorted({option for options in catalog.values() for option in options}),
                ),
                "volume": st.column_config.NumberColumn("수량", min_value=1, step=1),
                "mapping_status": st.column_config.TextColumn("매핑", disabled=True),
                "mapping_reason": st.column_config.TextColumn("검토 사유", disabled=True),
                "row_id": None,
            },
            key=f"{key_prefix}_items",
        )
        no_order = st.checkbox(
            "주문 없음이 맞습니다",
            value=bool(file_review.get("no_order_confirmed")),
            key=f"{key_prefix}_no_order",
        )
        buttons = st.columns(2)
        save_clicked = buttons[0].form_submit_button("임시저장", width="stretch")
        complete_clicked = buttons[1].form_submit_button(
            "이 파일 확인 완료", type="primary", width="stretch"
        )

    if not (save_clicked or complete_clicked):
        return
    _update_file_from_form(
        file_review,
        edited,
        order_name=order_name,
        phone=phone,
        address=address,
        search_address=search_address,
        zip_code=zip_code,
        no_order=no_order,
    )
    if complete_clicked:
        errors = validate_file_review(file_review, catalog)
        if errors:
            for error in errors:
                st.error(error)
            return
        file_review["review_status"] = classify_file_review(file_review, chat_data)
    try:
        save_review_draft(
            ctx.db_conn,
            snapshot["job_id"],
            ctx.user_id,
            int(snapshot.get("base_revision") or 0),
            snapshot,
        )
    except Exception as exc:
        st.error(f"임시저장에 실패했습니다: {exc}")
        return
    st.success("파일 검수 결과를 임시저장했습니다.")
    st.rerun()


def _items_frame(items: list[dict]) -> pd.DataFrame:
    columns = [
        "row_id", "raw_product", "raw_option", "product", "option", "volume",
        "mapping_status", "mapping_reason",
    ]
    return pd.DataFrame(items, columns=columns, dtype=object)


def _update_file_from_form(file_review: dict, edited: pd.DataFrame, **values) -> None:
    file_review["order_name"] = values["order_name"].strip() or None
    file_review["phone_number"] = format_phone_number(values["phone"].strip()) or None
    file_review["address"] = values["address"].strip() or None
    file_review["search_address"] = values["search_address"].strip() or None
    file_review["zip_code"] = normalize_zip_code(values["zip_code"])
    file_review["no_order_confirmed"] = bool(values["no_order"])
    items = []
    for row in edited.to_dict("records"):
        if not any(_clean_cell(row.get(key)) for key in ("raw_product", "product", "option", "volume")):
            continue
        items.append(
            {
                "row_id": _clean_cell(row.get("row_id")) or str(uuid.uuid4()),
                "order_number": None,
                "raw_product": _clean_cell(row.get("raw_product")),
                "raw_option": _clean_cell(row.get("raw_option")),
                "product": _clean_cell(row.get("product")),
                "option": _clean_cell(row.get("option")),
                "volume": _clean_volume(row.get("volume")),
                "mapping_status": _clean_cell(row.get("mapping_status")) or "manual",
                "mapping_reason": _clean_cell(row.get("mapping_reason")),
            }
        )
    file_review["items"] = items
    file_review["review_status"] = "unreviewed"


def _clean_cell(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        return value.strip() or None
    return value


def _clean_volume(value):
    if value is None or pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return value


def _render_confirmation(ctx: AppContext, job: dict, snapshot: dict) -> None:
    records = st.session_state.get(REVIEW_TRAINING_RECORDS) or []
    catalog = normalize_catalog(records[0].get("catalog_json")) if records else {}
    st.divider()
    if st.button("✅ 작업 전체 확정", type="primary", width="stretch"):
        errors = validate_snapshot(snapshot, catalog)
        if errors:
            st.error("아직 확정할 수 없습니다.")
            for error in errors:
                st.write(f"- {error}")
        else:
            _confirm(ctx, job, snapshot)

    confirmed_revision = st.session_state.get(REVIEW_CONFIRMED_REVISION, 0)
    if confirmed_revision and snapshot.get("orders") is not None:
        frame = _output_frame(snapshot_to_orders(snapshot), ctx.config)
        st.download_button(
            "📥 확정 XLSX 다운로드",
            data=write_excel_with_text_zipcode(
                frame, ctx.config["output"]["sheet_name"]
            ),
            file_name=f"{job['title']}_confirmed_r{confirmed_revision}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )


def _confirm(ctx: AppContext, job: dict, snapshot: dict) -> None:
    date_prefix = str(job.get("created_at") or "")[:10].replace("-", "") or None
    confirmed = renumber_snapshot(snapshot, date_prefix)
    confirmed["orders"] = snapshot_to_orders(confirmed)
    source_hash = snapshot_hash(confirmed)
    idempotency_key = snapshot_hash(
        {
            "job_id": snapshot["job_id"],
            "base_revision": snapshot.get("base_revision", 0),
            "source_hash": source_hash,
        }
    )
    try:
        result = confirm_review(
            ctx.db_conn,
            job_id=snapshot["job_id"],
            user_id=ctx.user_id,
            base_revision=int(snapshot.get("base_revision") or 0),
            snapshot=confirmed,
            labels=build_training_labels(confirmed),
            source_hash=source_hash,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        st.error(f"작업 확정에 실패했습니다: {exc}")
        return
    revision = int(result.get("revision") or snapshot.get("base_revision", 0) + 1)
    confirmed["base_revision"] = revision
    st.session_state[REVIEW_SNAPSHOT] = confirmed
    st.session_state[REVIEW_CONFIRMED_REVISION] = revision
    st.success(f"검수 결과를 revision {revision}로 확정했습니다.")
    st.rerun()


def _output_frame(rows: list[dict], config: dict) -> pd.DataFrame:
    frame = pd.DataFrame(rows, dtype=object)
    col_map = config["output_columns"]
    frame = frame.rename(columns={value: key for key, value in col_map.items() if value})
    frame = frame.reindex(columns=list(col_map.keys()), fill_value="")
    if "우편번호" in frame:
        frame["우편번호"] = frame["우편번호"].apply(normalize_zip_code)
    return frame
