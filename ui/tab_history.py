import json

import pandas as pd
import streamlit as st

from database import get_catalog_by_job, get_orders_by_job
from excel_utils import write_excel_with_text_zipcode
from services import normalize_catalog, normalize_zip_code
from ui.common import AppContext, select_job_ui


def render(ctx: AppContext) -> None:
    if not ctx.db_conn:
        st.warning("DB 연결이 설정되지 않아 이력을 불러올 수 없습니다.")
        return

    job = select_job_ui(
        ctx.db_conn,
        ctx.user_id,
        "다운로드할 이력을 선택하세요 (최근 5건)",
        key="history_job_radio",
    )
    if not job:
        st.info("저장된 추출 이력이 없습니다.")
        return

    orders = get_orders_by_job(conn=ctx.db_conn, job_id=job["id"])
    if not orders:
        st.info("해당 이력에 저장된 주문 데이터가 없습니다.")
        return

    frame = pd.DataFrame(orders, dtype=object)
    frame = frame.drop(
        columns=[c for c in ("id", "job_id", "created_at") if c in frame.columns]
    )
    col_map = ctx.config["output_columns"]
    frame = frame.rename(columns={value: key for key, value in col_map.items() if value})
    frame = frame.reindex(columns=list(col_map.keys()), fill_value="")
    if "우편번호" in frame:
        frame["우편번호"] = frame["우편번호"].apply(normalize_zip_code)

    st.dataframe(frame.head(20), width="stretch")
    st.caption(f"총 {len(frame)}건 (최대 20건 미리보기)")
    downloads = st.columns(2)
    with downloads[0]:
        st.download_button(
            label="📥 엑셀 파일(.xlsx) 다운로드",
            data=write_excel_with_text_zipcode(
                frame, ctx.config["output"]["sheet_name"]
            ),
            file_name=f"{job['title']}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            key="hist_download_btn",
            width="stretch",
        )
    with downloads[1]:
        stored = get_catalog_by_job(conn=ctx.db_conn, job_id=job["id"])
        if stored:
            catalog = normalize_catalog(json.loads(stored))
            st.download_button(
                label="📋 카탈로그(.json) 다운로드",
                data=json.dumps(catalog, ensure_ascii=False, indent=2).encode("utf-8"),
                file_name=f"catalog_{job['title']}.json",
                mime="application/json",
                type="secondary",
                key="hist_catalog_btn",
                width="stretch",
            )
        else:
            st.button(
                "📋 카탈로그 정보 없음",
                disabled=True,
                key="hist_catalog_btn_disabled",
                width="stretch",
            )
