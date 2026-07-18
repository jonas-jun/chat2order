from pathlib import Path

import pandas as pd
import streamlit as st

from excel_utils import write_excel_with_text_zipcode
from services import batch_lookup_zip_codes
from ui.common import AppContext


def render(ctx: AppContext) -> None:
    st.markdown(
        '<span class="step-badge">1</span> **엑셀 파일 업로드**',
        unsafe_allow_html=True,
    )
    source = st.file_uploader(
        "주소 컬럼이 포함된 엑셀 파일을 업로드하세요.",
        type=["xlsx", "xls"],
        key="zip_excel_uploader",
    )
    if not source:
        return

    frame = pd.read_excel(source)
    if "주소" not in frame.columns:
        st.error(f"'주소' 컬럼을 찾을 수 없습니다. 발견된 컬럼: {list(frame.columns)}")
        return

    st.markdown(
        '<span class="step-badge">2</span> **미리보기**',
        unsafe_allow_html=True,
    )
    has_zip = "우편번호" in frame.columns
    if has_zip:
        st.info("파일에 이미 '우편번호' 컬럼이 있습니다. 조회 결과로 덮어씁니다.")
    st.dataframe(frame.head(10), width="stretch")
    st.caption(f"총 {len(frame)}건")

    if not st.button(
        "📮 우편번호 조회 실행", type="primary", width="stretch", key="zip_lookup_btn"
    ):
        return
    if not ctx.api_key:
        st.warning("API Key가 할당되지 않았습니다. 관리자에게 문의하세요.")
        return
    if not ctx.juso_api_key:
        st.warning("도로명주소 API 키가 설정되지 않았습니다.")
        return

    with st.status("우편번호 조회 중입니다", expanded=True) as status:
        progress_text = st.empty()
        progress_bar = st.progress(0)

        def progress(index, total):
            if total:
                progress_bar.progress(min((index + 1) / total, 1.0))
            progress_text.write(f"📮 우편번호 조회 중... ({index + 1}/{total})")

        result = batch_lookup_zip_codes(
            df=frame,
            address_col="주소",
            juso_api_key=ctx.juso_api_key,
            api_key=ctx.api_key,
            model=ctx.config["gemini"]["model"],
            temperature=ctx.config["gemini"]["temperature"],
            prompt_template=ctx.address_to_search_prompt,
            progress_callback=progress,
        )
        if has_zip:
            frame["우편번호"] = result
        else:
            frame.insert(frame.columns.get_loc("주소") + 1, "우편번호", result)
        found = int((frame["우편번호"] != "").sum())
        status.update(
            label=f"🎉 우편번호 조회 완료! ({found}/{len(frame)}건 성공)",
            state="complete",
        )

    st.markdown(
        '<span class="step-badge">3</span> **결과 확인 및 다운로드**',
        unsafe_allow_html=True,
    )
    metrics = st.columns(3)
    metrics[0].metric("전체", len(frame))
    metrics[1].metric("성공", found)
    metrics[2].metric("미조회", len(frame) - found)
    st.dataframe(frame, width="stretch")
    st.download_button(
        label="📥 엑셀 파일(.xlsx) 다운로드",
        data=write_excel_with_text_zipcode(frame, "Sheet1"),
        file_name=f"{Path(source.name).stem}_우편번호.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        key="zip_download_btn",
    )
