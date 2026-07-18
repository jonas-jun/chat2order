import datetime
import json

import pandas as pd
import streamlit as st

from services import generate_catalog_from_csv
from ui.common import AppContext


def render(ctx: AppContext) -> None:
    st.markdown(
        '<span class="step-badge">1</span> **재고 CSV 업로드**',
        unsafe_allow_html=True,
    )
    source = st.file_uploader(
        "CSV 파일을 업로드하세요. (상품명·옵션내용 컬럼 필요)",
        type=["csv"],
        key="catalog_csv_uploader",
    )
    if not source:
        return

    try:
        catalog = generate_catalog_from_csv(source)
    except ValueError as exc:
        st.error(str(exc))
        return

    st.markdown(
        '<span class="step-badge">2</span> **미리보기 및 확인**',
        unsafe_allow_html=True,
    )
    columns = st.columns(3)
    columns[0].metric("총 상품 수", len(catalog))
    columns[1].metric("총 옵션 수", sum(len(options) for options in catalog.values()))
    columns[2].metric(
        "단일상품", sum(options == ["단일상품"] for options in catalog.values())
    )

    preview = pd.DataFrame(
        [
            {"상품명": product, "옵션": ", ".join(options), "옵션 수": len(options)}
            for product, options in catalog.items()
        ],
        dtype=object,
    )
    preview.index = preview.index + 1
    preview.index.name = "#"
    st.dataframe(preview, width="stretch")

    st.markdown(
        '<span class="step-badge">3</span> **카탈로그 다운로드**',
        unsafe_allow_html=True,
    )
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    st.download_button(
        label="📥 catalog.json 다운로드",
        data=json.dumps(catalog, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name=f"catalog_{timestamp}.json",
        mime="application/json",
        type="primary",
        width="stretch",
    )
