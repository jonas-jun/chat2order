"""자동 확정되지 않은 주문 항목만 보완하고 XLSX를 재생성하는 탭."""

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
    ensure_review_metadata,
    next_pending_review_file_id,
    normalize_catalog,
    renumber_snapshot,
    snapshot_hash,
    snapshot_to_orders,
    validate_file_review,
    validate_snapshot,
)
from services import normalize_zip_code
from session_keys import (
    REVIEW_CONFIRMED_REVISION,
    REVIEW_DEFAULT_JOB_ID,
    REVIEW_JOB_ID,
    REVIEW_NEXT_FILE_ID,
    REVIEW_SELECTED_FILE_ID,
    REVIEW_SNAPSHOT,
    REVIEW_TRAINING_RECORDS,
)
from ui.common import AppContext


STATUS_LABELS = {
    "unreviewed": "보완 필요",
    "accepted": "확인 완료",
    "corrected": "수정 완료",
    "business_corrected": "수정 완료",
    "no_order_confirmed": "주문 아님",
}


def render(ctx: AppContext) -> None:
    if not ctx.db_conn:
        st.warning("DB 연결이 설정되지 않아 보완 기능을 사용할 수 없습니다.")
        return

    try:
        jobs = get_jobs_by_user(ctx.db_conn, ctx.user_id, limit=20)
    except Exception as exc:
        st.error(f"추출 작업 목록을 불러오지 못했습니다: {exc}")
        return
    if not jobs:
        st.info("보완할 추출 작업이 없습니다. 먼저 주문서 추출을 실행해 주세요.")
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
        st.info("이 작업에는 저장된 추출 데이터가 없습니다.")
        return

    review_files = _review_files(snapshot)
    if not review_files:
        _render_no_issues(ctx, job, snapshot)
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


def _review_files(snapshot: dict) -> list[dict]:
    return [
        file_review
        for file_review in snapshot.get("files") or []
        if file_review.get("review_required")
    ]


def _select_job(jobs: list[dict]) -> dict | None:
    default_id = str(st.session_state.get(REVIEW_DEFAULT_JOB_ID) or "")
    current_id = str(st.session_state.get(REVIEW_JOB_ID) or default_id)
    index = next(
        (i for i, job in enumerate(jobs) if str(job["id"]) == current_id),
        0,
    )
    selected = st.selectbox(
        "보완할 추출 작업",
        options=range(len(jobs)),
        index=index,
        format_func=lambda i: _job_label(jobs[i]),
        key="review_job_select",
    )
    return jobs[selected] if selected is not None else None


def _job_label(job: dict) -> str:
    status = {
        "pending": "보완 대기",
        "in_progress": "보완 중",
        "confirmed": f"재생성 r{job.get('current_confirmed_revision') or 1}",
        "reopened": "재보완 중",
    }.get(job.get("review_status"), "미확인")
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
        snapshot = ensure_review_metadata(snapshot, records)
        snapshot["base_revision"] = revision
        st.session_state[REVIEW_JOB_ID] = str(job["id"])
        st.session_state[REVIEW_SNAPSHOT] = snapshot
        st.session_state[REVIEW_TRAINING_RECORDS] = records
        st.session_state[REVIEW_CONFIRMED_REVISION] = revision
        first = (_review_files(snapshot) or [{}])[0]
        st.session_state[REVIEW_SELECTED_FILE_ID] = first.get("training_data_id")
        return True
    except Exception as exc:
        st.error(
            "보완 데이터를 불러오지 못했습니다. "
            "`migrations/001_order_reviews.sql` 적용 여부를 확인해 주세요. "
            f"({exc})"
        )
        return False


def _render_summary(job: dict, snapshot: dict) -> None:
    files = _review_files(snapshot)
    completed = sum(
        1
        for file_review in files
        if file_review.get("review_status") in COMPLETED_FILE_STATUSES
    )
    issue_items = sum(
        sum(
            1
            for item in file_review.get("items") or []
            if item.get("requires_review")
        )
        for file_review in files
    )
    columns = st.columns(4)
    columns[0].metric("보완 대상 파일", len(files))
    columns[1].metric("보완 완료", f"{completed}/{len(files)}")
    columns[2].metric("불확정 항목", issue_items)
    columns[3].metric("기준 revision", snapshot.get("base_revision", 0))
    st.progress(completed / len(files) if files else 0)
    if job.get("review_status") == "confirmed":
        st.info("이미 재생성된 작업입니다. 다시 보완하면 새 revision으로 저장됩니다.")


def _select_file(snapshot: dict) -> dict | None:
    files = _review_files(snapshot)
    if not files:
        return None
    pending_next = st.session_state.pop(REVIEW_NEXT_FILE_ID, None)
    if pending_next:
        st.session_state[REVIEW_SELECTED_FILE_ID] = pending_next
    current = st.session_state.get(REVIEW_SELECTED_FILE_ID)
    index = next(
        (i for i, item in enumerate(files) if item["training_data_id"] == current),
        0,
    )
    selectbox_key = f"review_file_select_{snapshot['job_id']}"
    if pending_next:
        st.session_state[selectbox_key] = index
    selected_index = st.selectbox(
        "보완할 채팅 파일",
        options=range(len(files)),
        index=index,
        format_func=lambda i: (
            f"{STATUS_LABELS.get(files[i].get('review_status'), '보완 필요')} · "
            f"{files[i].get('chat_filename')}"
        ),
        key=selectbox_key,
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
    key_prefix = f"review_{snapshot['job_id']}_{file_review['training_data_id']}"

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

    for reason in file_review.get("review_reasons") or []:
        st.warning(reason)

    with st.expander("🤖 최초 모델 예측"):
        st.json(decode_json(record.get("predicted_json"), {}) or {})

    st.markdown("#### 고객정보 (읽기 전용)")
    customer_columns = st.columns(3)
    customer_columns[0].text_input(
        "수령자명",
        value=file_review.get("order_name") or "",
        disabled=True,
        key=f"{key_prefix}_readonly_name",
    )
    customer_columns[1].text_input(
        "전화번호",
        value=file_review.get("phone_number") or "",
        disabled=True,
        key=f"{key_prefix}_readonly_phone",
    )
    customer_columns[2].text_input(
        "우편번호",
        value=file_review.get("zip_code") or "",
        disabled=True,
        key=f"{key_prefix}_readonly_zip",
    )
    st.text_input(
        "주소",
        value=file_review.get("address") or "",
        disabled=True,
        key=f"{key_prefix}_readonly_address",
    )

    locked_items = [
        item
        for item in file_review.get("items") or []
        if not item.get("requires_review")
    ]
    review_items = [
        item
        for item in file_review.get("items") or []
        if item.get("requires_review")
    ]
    if locked_items:
        with st.expander(f"✅ 자동 확정 항목 {len(locked_items)}건 (수정 불가)"):
            st.dataframe(
                _items_frame(locked_items)[
                    ["raw_product", "raw_option", "product", "option", "volume"]
                ],
                width="stretch",
                hide_index=True,
            )

    st.markdown("#### 확인이 필요한 주문 항목")
    st.caption(
        "상품명을 먼저 선택하면 해당 상품의 옵션만 표시됩니다. "
        "자동 확정 항목은 변경되지 않습니다."
    )
    edited_rows = _render_review_item_cards(review_items, catalog, key_prefix)
    no_order = False
    if not locked_items:
        no_order = st.checkbox(
            "해당 불확정 항목은 실제 주문이 아닙니다",
            value=bool(file_review.get("no_order_confirmed")),
            key=f"{key_prefix}_no_order",
        )

    actions = st.columns(3)
    add_clicked = actions[0].button(
        "➕ 주문 항목 추가", key=f"{key_prefix}_add", width="stretch"
    )
    save_clicked = actions[1].button(
        "임시저장", key=f"{key_prefix}_save", width="stretch"
    )
    complete_clicked = actions[2].button(
        "이 파일 보완 완료",
        key=f"{key_prefix}_complete",
        type="primary",
        width="stretch",
    )

    if not (add_clicked or save_clicked or complete_clicked):
        return
    _update_file_from_rows(
        file_review,
        edited_rows,
        locked_items=locked_items,
        no_order=no_order,
    )
    if add_clicked:
        _append_blank_review_item(file_review)
        st.rerun()
    if complete_clicked:
        errors = validate_file_review(file_review, catalog)
        if errors:
            for error in errors:
                st.error(error)
            return
        for item in file_review.get("items") or []:
            if item.get("requires_review"):
                item["mapping_status"] = "manual"
                item["mapping_reason"] = None
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
    if complete_clicked:
        next_id = next_pending_review_file_id(
            snapshot, file_review["training_data_id"]
        )
        if next_id:
            st.session_state[REVIEW_NEXT_FILE_ID] = next_id
    st.success("보완 결과를 임시저장했습니다.")
    st.rerun()


def _items_frame(items: list[dict]) -> pd.DataFrame:
    columns = [
        "row_id",
        "source_position",
        "raw_product",
        "raw_option",
        "product",
        "option",
        "volume",
        "mapping_status",
        "mapping_reason",
    ]
    return pd.DataFrame(items, columns=columns, dtype=object)


def _render_review_item_cards(
    items: list[dict],
    catalog: dict[str, list[str]],
    key_prefix: str,
) -> list[dict]:
    rows = []
    for position, item in enumerate(items, start=1):
        row_id = str(item.get("row_id") or uuid.uuid4())
        widget_prefix = f"{key_prefix}_{row_id}"
        with st.container(border=True):
            st.caption(
                f"불확정 항목 {position} · "
                f"{item.get('mapping_reason') or item.get('mapping_status') or '확인 필요'}"
            )
            raw_columns = st.columns([2, 2, 1])
            raw_product = raw_columns[0].text_input(
                "원문 상품",
                value=item.get("raw_product") or "",
                key=f"{widget_prefix}_raw_product",
            )
            raw_option = raw_columns[1].text_input(
                "원문 옵션",
                value=item.get("raw_option") or "",
                key=f"{widget_prefix}_raw_option",
            )
            volume = raw_columns[2].number_input(
                "수량",
                min_value=1,
                step=1,
                value=_initial_volume(item.get("volume")),
                key=f"{widget_prefix}_volume",
            )

            product_key = f"{widget_prefix}_product"
            option_key = f"{widget_prefix}_option"
            product_choices = [""] + list(catalog.keys())
            product = st.selectbox(
                "상품명",
                options=product_choices,
                index=_choice_index(product_choices, item.get("product")),
                key=product_key,
                on_change=_reset_dependent_option,
                args=(product_key, option_key, catalog),
            )
            available_options = list(catalog.get(product, []))
            option_choices = [""] + available_options
            if option_key in st.session_state and st.session_state[option_key] not in option_choices:
                st.session_state[option_key] = (
                    available_options[0] if len(available_options) == 1 else ""
                )
            initial_option = item.get("option")
            if not initial_option and len(available_options) == 1:
                initial_option = available_options[0]
            option = st.selectbox(
                "옵션명",
                options=option_choices,
                index=_choice_index(option_choices, initial_option),
                key=option_key,
                disabled=not product or len(available_options) <= 1,
            )
            excluded = st.checkbox(
                "이 항목 제외",
                value=False,
                key=f"{widget_prefix}_excluded",
            )
        rows.append(
            {
                **item,
                "row_id": row_id,
                "raw_product": raw_product,
                "raw_option": raw_option,
                "product": product,
                "option": option,
                "volume": volume,
                "excluded": excluded,
            }
        )
    return rows


def _reset_dependent_option(
    product_key: str,
    option_key: str,
    catalog: dict[str, list[str]],
) -> None:
    options = list(catalog.get(st.session_state.get(product_key), []))
    current = st.session_state.get(option_key)
    if current not in options:
        st.session_state[option_key] = options[0] if len(options) == 1 else ""


def _choice_index(choices: list[str], value) -> int:
    try:
        return choices.index(value or "")
    except ValueError:
        return 0


def _initial_volume(value) -> int:
    cleaned = _clean_volume(value)
    return cleaned if isinstance(cleaned, int) and cleaned > 0 else 1


def _append_blank_review_item(file_review: dict) -> None:
    next_position = max(
        (int(item.get("source_position", -1)) for item in file_review.get("items") or []),
        default=-1,
    ) + 1
    file_review.setdefault("items", []).append(
        {
            "row_id": str(uuid.uuid4()),
            "source_position": next_position,
            "order_number": None,
            "raw_product": None,
            "raw_option": None,
            "product": None,
            "option": None,
            "volume": 1,
            "mapping_status": "manual",
            "mapping_reason": "사용자가 추가한 주문 항목",
            "requires_review": True,
        }
    )
    file_review["review_status"] = "unreviewed"


def _update_file_from_rows(file_review: dict, edited_rows: list[dict], **values) -> None:
    file_review["no_order_confirmed"] = bool(values["no_order"])
    items = list(values["locked_items"])
    next_position = max(
        (int(item.get("source_position", -1)) for item in file_review.get("items") or []),
        default=-1,
    ) + 1
    for row in edited_rows:
        if row.get("excluded"):
            continue
        if not any(
            _clean_cell(row.get(key))
            for key in ("raw_product", "product", "option", "volume")
        ):
            continue
        position = _clean_position(row.get("source_position"), next_position)
        next_position = max(next_position, position + 1)
        items.append(
            {
                "row_id": _clean_cell(row.get("row_id")) or str(uuid.uuid4()),
                "source_position": position,
                "order_number": None,
                "raw_product": _clean_cell(row.get("raw_product")),
                "raw_option": _clean_cell(row.get("raw_option")),
                "product": _clean_cell(row.get("product")),
                "option": _clean_cell(row.get("option")),
                "volume": _clean_volume(row.get("volume")),
                "mapping_status": _clean_cell(row.get("mapping_status")) or "manual",
                "mapping_reason": _clean_cell(row.get("mapping_reason")),
                "requires_review": True,
            }
        )
    file_review["items"] = sorted(
        items, key=lambda item: int(item.get("source_position", 0))
    )
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


def _clean_position(value, fallback: int) -> int:
    if value is None or pd.isna(value):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback


def _render_confirmation(ctx: AppContext, job: dict, snapshot: dict) -> None:
    records = st.session_state.get(REVIEW_TRAINING_RECORDS) or []
    catalog = normalize_catalog(records[0].get("catalog_json")) if records else {}
    st.divider()
    if st.button(
        "🔄 보완 완료 및 주문서 다시 생성", type="primary", width="stretch"
    ):
        errors = validate_snapshot(snapshot, catalog)
        if errors:
            st.error("아직 주문서를 다시 생성할 수 없습니다.")
            for error in errors:
                st.write(f"- {error}")
        else:
            _confirm(ctx, job, snapshot)

    confirmed_revision = st.session_state.get(REVIEW_CONFIRMED_REVISION, 0)
    if confirmed_revision and snapshot.get("orders") is not None:
        frame = _output_frame(snapshot_to_orders(snapshot), ctx.config)
        st.download_button(
            "📥 재생성된 XLSX 다운로드",
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
        st.error(f"주문서 재생성에 실패했습니다: {exc}")
        return
    revision = int(result.get("revision") or snapshot.get("base_revision", 0) + 1)
    confirmed["base_revision"] = revision
    st.session_state[REVIEW_SNAPSHOT] = confirmed
    st.session_state[REVIEW_CONFIRMED_REVISION] = revision
    st.success(f"보완 결과를 revision {revision}로 저장했습니다.")
    st.rerun()


def _render_no_issues(ctx: AppContext, job: dict, snapshot: dict) -> None:
    st.success("모든 주문 항목이 자동 확정되었습니다. 보완할 항목이 없습니다.")
    frame = _output_frame(snapshot_to_orders(snapshot), ctx.config)
    if frame.empty:
        st.info("생성할 주문이 없습니다.")
        return
    st.download_button(
        "📥 현재 주문서 다운로드",
        data=write_excel_with_text_zipcode(
            frame, ctx.config["output"]["sheet_name"]
        ),
        file_name=f"{job['title']}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )


def _output_frame(rows: list[dict], config: dict) -> pd.DataFrame:
    frame = pd.DataFrame(rows, dtype=object)
    col_map = config["output_columns"]
    frame = frame.rename(columns={value: key for key, value in col_map.items() if value})
    frame = frame.reindex(columns=list(col_map.keys()), fill_value="")
    if "우편번호" in frame:
        frame["우편번호"] = frame["우편번호"].apply(normalize_zip_code)
    return frame
